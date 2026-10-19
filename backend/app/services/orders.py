import hashlib
import json
import logging
from decimal import Decimal, InvalidOperation, localcontext

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.permissions import AccessDenied, AuthorizationService, OrderAction
from app.modules.catalog.models import Sku, Spu
from app.modules.inventory.models import Stock, StockMovement, Warehouse, WarehouseAccess
from app.modules.orders.models import Order, OrderItem
from app.modules.orders.schemas import (
    OrderItemCreate, OrderItemResponse, OrderPage, OrderResponse,
)
from app.modules.shops.models import Shop
from app.modules.users.models import User
from app.services.audit import AuditService
from app.services.identity import IdentityConflict, IdentityEligibilityError, IdentityService, Role
from app.services.inventory import (
    MAX_BIGINT, InventoryConflict, checked_quantity_after, lock_stock_row,
    validate_idempotency_key,
)
from app.services.warehouse_access import is_authorized

logger = logging.getLogger(__name__)
MAX_ITEMS = 100
MAX_AMOUNT = Decimal("999999999999999999999999999999999999.99")


class OrderForbidden(Exception):
    pass


class OrderNotFound(Exception):
    pass


class OrderConflict(Exception):
    pass


def _end_read(session: Session) -> None:
    if session.new or session.dirty or session.deleted:
        raise RuntimeError("cannot discard a Session containing pending changes")
    if session.in_transaction():
        session.rollback()


def _order_response(session: Session, order: Order, rows: list[OrderItem] | None = None) -> OrderResponse:
    if rows is None:
        rows = session.scalars(
            select(OrderItem).where(OrderItem.order_id == order.id).order_by(OrderItem.id)
        ).all()
    return OrderResponse(
        id=order.id, merchant_id=order.merchant_id, shop_id=order.shop_id,
        created_by_user_id=order.created_by_user_id, status=order.status,
        total_amount=order.total_amount, created_at=order.created_at,
        completed_at=order.completed_at, cancelled_at=order.cancelled_at,
        items=[OrderItemResponse.model_validate(row, from_attributes=True) for row in rows],
    )


def _lock_user_identity(session: Session, actor_user_id: int):
    user = session.scalar(
        select(User).where(User.id == actor_user_id).with_for_update()
        .execution_options(populate_existing=True)
    )
    if user is None or not user.is_active:
        raise OrderForbidden("active order operator required")
    try:
        return user, IdentityService.get_identity(session, user)
    except (IdentityConflict, IdentityEligibilityError) as exc:
        raise OrderForbidden("current database identity required") from exc


def _authorized_shop(session: Session, identity, shop_id: int, action: OrderAction) -> tuple[Shop, int]:
    try:
        merchant_id = AuthorizationService.check_order_action(session, identity, shop_id=shop_id, action=action)
    except AccessDenied as exc:
        raise OrderForbidden("no access to this order") from exc
    shop = session.scalar(
        select(Shop).where(Shop.id == shop_id).with_for_update()
        .execution_options(populate_existing=True)
    )
    if shop is None or shop.merchant_id != merchant_id:
        raise OrderForbidden("no access to this order")
    return shop, int(merchant_id)


def _request_hash(actor_user_id: int, merchant_id: int, shop_id: int, items: list[tuple[int, int]]) -> str:
    payload = {
        "actor_user_id": actor_user_id, "merchant_id": merchant_id, "shop_id": shop_id,
        "items": [{"sku_id": sku_id, "quantity": quantity} for sku_id, quantity in sorted(items)],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()


def _normalized_items(items: list[OrderItemCreate]) -> list[tuple[int, int]]:
    if not 1 <= len(items) <= MAX_ITEMS:
        raise OrderConflict("orders require between 1 and 100 SKU items")
    normalized = [(item.sku_id, item.quantity) for item in items]
    if any(type(sku_id) is not int or not 1 <= sku_id <= MAX_BIGINT
           or type(quantity) is not int or not 1 <= quantity <= MAX_BIGINT
           for sku_id, quantity in normalized):
        raise OrderConflict("SKU IDs and quantities must be positive BIGINT integers")
    if len({sku_id for sku_id, _ in normalized}) != len(normalized):
        raise OrderConflict("duplicate SKU IDs are not allowed")
    return sorted(normalized)


def _find_replay(session: Session, *, shop_id: int, request_key: str, request_hash: str):
    # A locking predicate read for a missing (shop_id, request_key) can acquire
    # an InnoDB gap lock on the unique index and deadlock unrelated shops while
    # they wait on their own warehouse rows. Probe without a range lock, then use
    # the immutable primary key for a current locking read only when a row exists.
    existing_id = session.scalar(
        select(Order.id).where(Order.shop_id == shop_id, Order.request_key == request_key)
    )
    if existing_id is None:
        return None
    existing = session.scalar(
        select(Order).where(Order.id == existing_id).with_for_update()
        .execution_options(populate_existing=True)
    )
    if existing is None or existing.shop_id != shop_id or existing.request_key != request_key:
        return None
    if existing.request_hash != request_hash:
        raise OrderConflict("Idempotency-Key was already used for a different order request")
    return _order_response(session, existing)


def _audit_safely(audit_writer, *, actor_user_id: int, event_type: str,
                  order: OrderResponse, outcome: str) -> None:
    try:
        audit_writer(
            actor_user_id=actor_user_id, event_type=event_type, target_type="order",
            target_id=order.id, outcome=outcome,
            event_data={"merchant_id": order.merchant_id, "shop_id": order.shop_id,
                        "order_id": order.id, "actor_user_id": actor_user_id},
        )
    except Exception as exc:
        logger.error("order audit failed event_type=%s order_id=%s error_type=%s",
                     event_type, order.id, type(exc).__name__)


class OrderService:
    @staticmethod
    def create(session: Session, *, actor_user_id: int, items: list[OrderItemCreate],
               idempotency_key: str, audit_writer=AuditService.record_after_commit) -> OrderResponse:
        key = validate_idempotency_key(idempotency_key)
        normalized = _normalized_items(items)
        if session.new or session.dirty or session.deleted:
            raise RuntimeError("cannot discard a Session containing pending changes")
        # Auth dependencies use autobegin. End their read transaction before the
        # first write lock so identity is re-read after the current User lock.
        _end_read(session)
        event_type = None
        response = None
        try:
            with session.begin():
                user, identity = _lock_user_identity(session, actor_user_id)
                if identity.role not in (Role.SHOP_OWNER, Role.SHOP_EMPLOYEE) or identity.shop_id is None:
                    raise OrderForbidden("current shop owner or employee required")
                shop, merchant_id = _authorized_shop(session, identity, int(identity.shop_id), OrderAction.CREATE)
                request_hash = _request_hash(actor_user_id, merchant_id, shop.id, normalized)

                # Lock/current-read the idempotency record before product status,
                # price, warehouse or stock checks. Historical retries replay the
                # original snapshot even after catalog or stock state changes.
                replay = _find_replay(session, shop_id=shop.id, request_key=key, request_hash=request_hash)
                if replay is not None:
                    response = replay
                else:
                    warehouse = session.scalar(
                        select(Warehouse).where(Warehouse.merchant_id == merchant_id).with_for_update()
                        .execution_options(populate_existing=True)
                    )
                    if warehouse is None or not is_authorized(session, shop.id):
                        raise OrderForbidden("shop is not authorized to use merchant warehouse")

                    sku_ids = [sku_id for sku_id, _ in normalized]
                    sku_parent_rows = session.execute(
                        select(Sku.id, Sku.spu_id, Sku.merchant_id).where(Sku.id.in_(sku_ids))
                    ).all()
                    if len(sku_parent_rows) != len(sku_ids) or any(row.merchant_id != merchant_id for row in sku_parent_rows):
                        raise OrderNotFound("one or more SKUs are not available to this merchant")
                    parent_by_sku = {row.id: int(row.spu_id) for row in sku_parent_rows}
                    spu_ids = sorted(set(parent_by_sku.values()))
                    spus = session.scalars(
                        select(Spu).where(Spu.id.in_(spu_ids), Spu.merchant_id == merchant_id)
                        .order_by(Spu.id).with_for_update().execution_options(populate_existing=True)
                    ).all()
                    if len(spus) != len(spu_ids) or any(spu.status != "ON_SALE" for spu in spus):
                        raise OrderConflict("all order products must be ON_SALE")
                    spu_by_id = {spu.id: spu for spu in spus}
                    skus = session.scalars(
                        select(Sku).where(Sku.id.in_(sku_ids), Sku.merchant_id == merchant_id)
                        .order_by(Sku.id).with_for_update().execution_options(populate_existing=True)
                    ).all()
                    if len(skus) != len(sku_ids) or any(parent_by_sku.get(sku.id) != sku.spu_id for sku in skus):
                        raise OrderNotFound("one or more SKUs are not available to this merchant")

                    stocks = {}
                    for sku_id in sku_ids:
                        stocks[sku_id] = lock_stock_row(
                            session, merchant_id=merchant_id, warehouse_id=warehouse.id, sku_id=sku_id,
                        )

                    sku_by_id = {sku.id: sku for sku in skus}
                    with localcontext() as ctx:
                        ctx.prec = 50
                        item_values = []
                        total = Decimal("0.00")
                        for sku_id, quantity in normalized:
                            sku = sku_by_id[sku_id]
                            price = Decimal(sku.price)
                            if not price.is_finite() or price < 0 or price.as_tuple().exponent < -2:
                                raise OrderConflict("SKU price is outside supported DECIMAL(12,2) range")
                            subtotal = price * Decimal(quantity)
                            if subtotal > Decimal("9" * 30 + ".99"):
                                raise OrderConflict("order item subtotal exceeds database DECIMAL range")
                            total += subtotal
                            item_values.append((sku_id, quantity, price, subtotal, sku, spu_by_id[sku.spu_id]))
                        if not total.is_finite() or total > MAX_AMOUNT:
                            raise OrderConflict("order total exceeds database DECIMAL range")

                    # Validate every locked quantity before mutating any row.
                    after_by_sku = {}
                    try:
                        for sku_id, quantity in normalized:
                            after_by_sku[sku_id] = checked_quantity_after(stocks[sku_id].quantity, -quantity)
                    except InventoryConflict as exc:
                        raise OrderConflict(str(exc)) from exc

                    order = Order(
                        merchant_id=merchant_id, shop_id=shop.id, created_by_user_id=user.id,
                        status="PENDING", total_amount=total, request_key=key, request_hash=request_hash,
                    )
                    session.add(order)
                    session.flush()
                    order_items = []
                    for sku_id, quantity, price, subtotal, sku, spu in item_values:
                        item = OrderItem(
                            order_id=order.id, merchant_id=merchant_id, sku_id=sku_id, quantity=quantity,
                            unit_price=price, subtotal=subtotal, sku_code_snapshot=sku.sku_code,
                            spu_name_snapshot=spu.name, specification_snapshot=sku.specification,
                        )
                        order_items.append(item)
                        session.add(item)
                    session.flush()
                    for item in order_items:
                        stock = stocks[item.sku_id]
                        before = stock.quantity
                        after = after_by_sku[item.sku_id]
                        stock.quantity = after
                        session.add(StockMovement(
                            merchant_id=merchant_id, warehouse_id=warehouse.id, sku_id=item.sku_id,
                            actor_user_id=user.id, operation_type="ORDER_DEDUCT", order_item_id=item.id,
                            quantity_delta=-item.quantity, quantity_before=before, quantity_after=after,
                            idempotency_key=None, request_hash=None,
                        ))
                    session.flush()
                    session.refresh(order)
                    response = _order_response(session, order)
                    event_type = "order_created"
        except IntegrityError as exc:
            # The Shop row serializes same-key requests; retain a safe fallback for
            # a database-level race and never continue in a failed transaction.
            if getattr(getattr(exc, "orig", None), "args", [None])[0] == 1062:
                with session.begin():
                    replay = _find_replay(session, shop_id=int(identity.shop_id), request_key=key,
                                          request_hash=_request_hash(actor_user_id, int(identity.merchant_id),
                                                                    int(identity.shop_id), normalized))
                if replay is not None:
                    return replay
                raise OrderConflict("order request conflicts with an existing record") from exc
            raise
        if event_type and response:
            _audit_safely(audit_writer, actor_user_id=actor_user_id, event_type=event_type,
                          order=response, outcome="PENDING")
        return response

    @staticmethod
    def list_orders(session: Session, *, identity, shop_id: int | None, status_filter: str | None,
                    limit: int, offset: int) -> OrderPage:
        if identity.role not in (Role.MERCHANT_ADMIN, Role.SHOP_OWNER, Role.SHOP_EMPLOYEE):
            raise OrderForbidden("orders are unavailable for this identity")
        conditions = []
        if identity.role in (Role.SHOP_OWNER, Role.SHOP_EMPLOYEE):
            if identity.shop_id is None or (shop_id is not None and shop_id != identity.shop_id):
                raise OrderForbidden("orders are limited to the current shop")
            conditions.append(Order.shop_id == identity.shop_id)
        else:
            if identity.merchant_id is None:
                raise OrderForbidden("merchant identity required")
            conditions.append(Order.merchant_id == identity.merchant_id)
            if shop_id is not None:
                valid_shop = session.scalar(select(Shop.id).where(
                    Shop.id == shop_id, Shop.merchant_id == identity.merchant_id
                ))
                if valid_shop is None:
                    raise OrderForbidden("shop filter is outside current merchant")
                conditions.append(Order.shop_id == shop_id)
        if status_filter is not None:
            conditions.append(Order.status == status_filter)
        total = session.scalar(select(func.count(Order.id)).where(*conditions)) or 0
        rows = session.scalars(select(Order).where(*conditions)
                               .order_by(Order.created_at.desc(), Order.id.desc())
                               .limit(limit).offset(offset)).all()
        items_by_order: dict[int, list[OrderItem]] = {}
        if rows:
            item_rows = session.scalars(
                select(OrderItem).where(OrderItem.order_id.in_([row.id for row in rows]))
                .order_by(OrderItem.order_id, OrderItem.id)
            ).all()
            for item in item_rows:
                items_by_order.setdefault(item.order_id, []).append(item)
        return OrderPage(items=[_order_response(session, row, items_by_order.get(row.id, [])) for row in rows],
                         limit=limit, offset=offset, total=total)

    @staticmethod
    def get_order(session: Session, *, identity, order_id: int) -> OrderResponse:
        row = session.scalar(select(Order).where(Order.id == order_id))
        if row is None:
            raise OrderNotFound("order not found")
        try:
            AuthorizationService.check_order_action(session, identity, shop_id=row.shop_id, action=OrderAction.READ)
        except AccessDenied as exc:
            raise OrderNotFound("order not found") from exc
        return _order_response(session, row)

    @staticmethod
    def _prepare_transition(session: Session, order_id: int, expected_shop_id: int):
        row = session.execute(select(Order.id, Order.merchant_id, Order.shop_id)
                              .where(Order.id == order_id, Order.shop_id == expected_shop_id)).one_or_none()
        if row is None:
            raise OrderNotFound("order not found")
        item_rows = session.execute(
            select(OrderItem.sku_id, Sku.spu_id).join(Sku, Sku.id == OrderItem.sku_id)
            .where(OrderItem.order_id == order_id).order_by(OrderItem.sku_id)
        ).all()
        if not item_rows:
            raise OrderConflict("order has no inventory items")
        return int(row.merchant_id), int(row.shop_id), [(int(item.sku_id), int(item.spu_id)) for item in item_rows]

    @staticmethod
    def _transition(session: Session, *, actor_user_id: int, order_id: int, expected_shop_id: int, action: str,
                    audit_writer=AuditService.record_after_commit) -> OrderResponse:
        if session.new or session.dirty or session.deleted:
            raise RuntimeError("cannot discard a Session containing pending changes")
        merchant_id, shop_id, captured_items = OrderService._prepare_transition(session, order_id, expected_shop_id)
        _end_read(session)
        event_type = None
        response = None
        with session.begin():
            user, identity = _lock_user_identity(session, actor_user_id)
            if identity.role not in (Role.SHOP_OWNER, Role.SHOP_EMPLOYEE):
                raise OrderForbidden("only this shop owner or employee can change orders")
            shop, current_merchant = _authorized_shop(session, identity, shop_id, OrderAction(action.lower()))
            if current_merchant != merchant_id:
                raise OrderNotFound("order not found")
            warehouse = session.scalar(
                select(Warehouse).where(Warehouse.merchant_id == merchant_id).with_for_update()
                .execution_options(populate_existing=True)
            )
            if warehouse is None:
                raise OrderConflict("merchant warehouse is not configured")
            spu_ids = sorted({spu_id for _sku_id, spu_id in captured_items})
            spus = session.scalars(select(Spu).where(Spu.id.in_(spu_ids), Spu.merchant_id == merchant_id)
                                   .order_by(Spu.id).with_for_update()
                                   .execution_options(populate_existing=True)).all()
            if len(spus) != len(spu_ids):
                raise OrderConflict("order product tenant relationship is invalid")
            sku_ids = sorted({sku_id for sku_id, _spu_id in captured_items})
            skus = session.scalars(select(Sku).where(Sku.id.in_(sku_ids), Sku.merchant_id == merchant_id)
                                   .order_by(Sku.id).with_for_update()
                                   .execution_options(populate_existing=True)).all()
            if len(skus) != len(sku_ids):
                raise OrderConflict("order SKU relationship is invalid")
            stocks = {}
            for sku_id in sku_ids:
                stocks[sku_id] = lock_stock_row(
                    session, merchant_id=merchant_id, warehouse_id=warehouse.id, sku_id=sku_id,
                )
            order = session.scalar(select(Order).where(Order.id == order_id, Order.shop_id == shop_id)
                                   .with_for_update().execution_options(populate_existing=True))
            if order is None:
                raise OrderNotFound("order not found")
            if order.status != "PENDING":
                raise OrderConflict(f"only PENDING orders can be {action}d")
            if action == "complete":
                order.status = "COMPLETED"
                order.completed_at = func.current_timestamp()
                outcome = "COMPLETED"
                event_type = "order_completed"
            else:
                items = session.scalars(select(OrderItem).where(OrderItem.order_id == order.id)
                                        .order_by(OrderItem.sku_id)).all()
                # Check all overflow boundaries before any update or movement insert.
                restore_values = {}
                try:
                    for item in items:
                        restore_values[item.sku_id] = checked_quantity_after(stocks[item.sku_id].quantity,
                                                                             item.quantity)
                except InventoryConflict as exc:
                    raise OrderConflict(str(exc)) from exc
                for item in items:
                    stock = stocks[item.sku_id]
                    before = stock.quantity
                    after = restore_values[item.sku_id]
                    stock.quantity = after
                    session.add(StockMovement(
                        merchant_id=merchant_id, warehouse_id=warehouse.id, sku_id=item.sku_id,
                        actor_user_id=user.id, operation_type="ORDER_RESTORE", order_item_id=item.id,
                        quantity_delta=item.quantity, quantity_before=before, quantity_after=after,
                        idempotency_key=None, request_hash=None,
                    ))
                order.status = "CANCELLED"
                order.cancelled_at = func.current_timestamp()
                outcome = "CANCELLED"
                event_type = "order_cancelled"
            session.flush()
            session.refresh(order)
            response = _order_response(session, order)
        _audit_safely(audit_writer, actor_user_id=actor_user_id, event_type=event_type,
                      order=response, outcome=outcome)
        return response

    @staticmethod
    def complete(session: Session, *, actor_user_id: int, order_id: int, expected_shop_id: int,
                 audit_writer=AuditService.record_after_commit) -> OrderResponse:
        return OrderService._transition(session, actor_user_id=actor_user_id, order_id=order_id, expected_shop_id=expected_shop_id,
                                        action="complete", audit_writer=audit_writer)

    @staticmethod
    def cancel(session: Session, *, actor_user_id: int, order_id: int, expected_shop_id: int,
               audit_writer=AuditService.record_after_commit) -> OrderResponse:
        return OrderService._transition(session, actor_user_id=actor_user_id, order_id=order_id, expected_shop_id=expected_shop_id,
                                        action="cancel", audit_writer=audit_writer)


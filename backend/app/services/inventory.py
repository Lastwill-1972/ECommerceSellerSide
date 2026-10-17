import hashlib
import json
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.permissions import AccessDenied, AuthorizationService
from app.modules.catalog.models import Sku, Spu
from app.modules.inventory.models import Stock, StockMovement, Warehouse, WarehouseAccess
from app.modules.inventory.schemas import (
    StockMovementPage, StockMovementResponse, StockOperationResponse, StockPage,
    StockResponse, WarehouseResponse,
)
from app.modules.shops.models import Shop
from app.modules.users.models import User
from app.services.audit import AuditService
from app.services.identity import IdentityConflict, IdentityEligibilityError, IdentityService, Role

MAX_BIGINT = 9_223_372_036_854_775_807
MIN_BIGINT = -9_223_372_036_854_775_808


class InventoryForbidden(Exception):
    pass


class InventoryNotFound(Exception):
    pass


class InventoryConflict(Exception):
    pass


def _end_read(session: Session) -> None:
    if session.new or session.dirty or session.deleted:
        raise RuntimeError("cannot discard a Session containing pending changes")
    if session.in_transaction():
        session.rollback()


def validate_idempotency_key(key: str | None) -> str:
    if not isinstance(key, str) or not 1 <= len(key) <= 128:
        raise InventoryConflict("Idempotency-Key must contain 1 to 128 visible ASCII characters")
    if any(ord(char) < 33 or ord(char) > 126 for char in key):
        raise InventoryConflict("Idempotency-Key must not contain whitespace or non-ASCII characters")
    return key


def _operation_response(row: StockMovement) -> StockOperationResponse:
    return StockOperationResponse(
        movement_id=row.id, sku_id=row.sku_id, quantity_before=row.quantity_before,
        quantity_delta=row.quantity_delta, quantity_after=row.quantity_after,
        idempotency_key=row.idempotency_key,
    )


def _movement_response(row: StockMovement) -> StockMovementResponse:
    return StockMovementResponse(
        movement_id=row.id, sku_id=row.sku_id, quantity_before=row.quantity_before,
        quantity_delta=row.quantity_delta, quantity_after=row.quantity_after,
        idempotency_key=row.idempotency_key, merchant_id=row.merchant_id,
        warehouse_id=row.warehouse_id, actor_user_id=row.actor_user_id,
        operation_type=row.operation_type, order_item_id=row.order_item_id,
        created_at=row.created_at,
    )


def _lock_merchant_admin(session: Session, actor_user_id: int) -> tuple[User, int]:
    user = session.scalar(
        select(User).where(User.id == actor_user_id).with_for_update()
        .execution_options(populate_existing=True)
    )
    if user is None or not user.is_active:
        raise InventoryForbidden("active merchant administrator required")
    try:
        identity = IdentityService.get_identity(session, user)
    except (IdentityConflict, IdentityEligibilityError) as exc:
        raise InventoryForbidden("merchant administrator required") from exc
    try:
        merchant_id = AuthorizationService.catalog_merchant(identity, write=True)
    except AccessDenied as exc:
        raise InventoryForbidden("merchant administrator required") from exc
    return user, int(merchant_id)


def _duplicate(exc: IntegrityError) -> bool:
    args = getattr(getattr(exc, "orig", None), "args", ())
    return bool(args and args[0] == 1062)


def _request_hash(*, actor_user_id: int, merchant_id: int, warehouse_id: int, sku_id: int, delta: int) -> str:
    payload = {
        "actor_user_id": actor_user_id, "merchant_id": merchant_id,
        "warehouse_id": warehouse_id, "sku_id": sku_id,
        "operation_type": "MANUAL_ADJUSTMENT", "delta": delta,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()


def lock_stock_row(session: Session, *, merchant_id: int, warehouse_id: int, sku_id: int) -> Stock:
    """Lock one stock row after the caller has taken earlier locks in global order."""
    row = session.scalar(
        select(Stock).where(Stock.merchant_id == merchant_id, Stock.warehouse_id == warehouse_id,
                            Stock.sku_id == sku_id)
        .with_for_update().execution_options(populate_existing=True)
    )
    if row is None:
        raise InventoryConflict("SKU stock record is not initialized")
    return row


def checked_quantity_after(quantity: int, delta: int) -> int:
    """Validate a signed BIGINT adjustment without overflowing MySQL BIGINT."""
    if (type(quantity) is not int or not 0 <= quantity <= MAX_BIGINT
            or type(delta) is not int or not MIN_BIGINT <= delta <= MAX_BIGINT or delta == 0):
        raise InventoryConflict("quantity and delta must be valid nonzero BIGINT integers")
    result = quantity + delta
    if result < 0:
        raise InventoryConflict("stock quantity cannot become negative")
    if result > MAX_BIGINT:
        raise InventoryConflict("stock quantity exceeds BIGINT range")
    return result


class InventoryService:
    @staticmethod
    def warehouse(session: Session, *, identity):
        merchant_id = InventoryService._readable_merchant(session, identity)
        row = session.scalar(select(Warehouse).where(Warehouse.merchant_id == merchant_id))
        if row is None:
            raise InventoryNotFound("warehouse not found")
        return WarehouseResponse.model_validate(row, from_attributes=True)

    @staticmethod
    def _readable_merchant(session: Session, identity) -> int:
        if identity.role is Role.MERCHANT_ADMIN and identity.merchant_id is not None:
            return int(identity.merchant_id)
        if identity.role in (Role.SHOP_OWNER, Role.SHOP_EMPLOYEE) and identity.shop_id is not None:
            shop = session.scalar(select(Shop).where(Shop.id == identity.shop_id))
            if shop is None or shop.merchant_id != identity.merchant_id:
                raise InventoryForbidden("shop inventory access required")
            authorized = session.scalar(
                select(WarehouseAccess.id).where(
                    WarehouseAccess.shop_id == shop.id,
                    WarehouseAccess.merchant_id == shop.merchant_id,
                    WarehouseAccess.warehouse_id == select(Warehouse.id).where(
                        Warehouse.merchant_id == shop.merchant_id
                    ).scalar_subquery(),
                ).limit(1)
            )
            if authorized is None:
                raise InventoryForbidden("shop warehouse access required")
            return int(shop.merchant_id)
        raise InventoryForbidden("no inventory read access for this identity")

    @staticmethod
    def stocks(session: Session, *, identity, limit: int, offset: int):
        merchant_id = InventoryService._readable_merchant(session, identity)
        total = session.scalar(select(func.count(Stock.id)).join(Sku, Sku.id == Stock.sku_id)
                               .join(Spu, Spu.id == Sku.spu_id).where(Stock.merchant_id == merchant_id)) or 0
        rows = session.execute(
            select(Stock.sku_id, Sku.sku_code, Sku.specification, Spu.id, Spu.name,
                   Stock.warehouse_id, Stock.quantity)
            .join(Sku, Sku.id == Stock.sku_id).join(Spu, Spu.id == Sku.spu_id)
            .where(Stock.merchant_id == merchant_id)
            .order_by(Stock.sku_id).limit(limit).offset(offset)
        ).all()
        return StockPage(
            items=[StockResponse(sku_id=r[0], sku_code=r[1], specification=r[2], spu_id=r[3],
                                 spu_name=r[4], warehouse_id=r[5], quantity=r[6]) for r in rows],
            limit=limit, offset=offset, total=total,
        )

    @staticmethod
    def movements(session: Session, *, identity, sku_id: int | None, limit: int, offset: int):
        if identity.role is not Role.MERCHANT_ADMIN or identity.merchant_id is None:
            raise InventoryForbidden("merchant administrator required")
        scope = [StockMovement.merchant_id == identity.merchant_id]
        if sku_id is not None:
            scope.append(StockMovement.sku_id == sku_id)
        total = session.scalar(select(func.count(StockMovement.id)).where(*scope)) or 0
        rows = session.scalars(
            select(StockMovement).where(*scope)
            .order_by(StockMovement.id.desc()).limit(limit).offset(offset)
        ).all()
        return StockMovementPage(items=[_movement_response(row) for row in rows], limit=limit,
                                 offset=offset, total=total)

    @staticmethod
    def adjust(session: Session, *, actor_user_id: int, sku_id: int, delta: int, idempotency_key: str,
               audit_writer=AuditService.record_after_commit):
        key = validate_idempotency_key(idempotency_key)
        if type(sku_id) is not int or sku_id < 1 or type(delta) is not int or not MIN_BIGINT <= delta <= MAX_BIGINT or delta == 0:
            raise InventoryConflict("invalid SKU or delta")
        if session.new or session.dirty or session.deleted:
            raise RuntimeError("cannot discard a Session containing pending changes")
        # Capture immutable parent IDs, then discard auth's autobegun read transaction.
        parent_spu_id = session.scalar(select(Sku.spu_id).where(Sku.id == sku_id))
        _end_read(session)
        if parent_spu_id is None:
            raise InventoryNotFound("SKU not found")

        merchant_id = warehouse_id = movement_id = None
        replay = False
        try:
            with session.begin():
                _user, merchant_id = _lock_merchant_admin(session, actor_user_id)
                warehouse = session.scalar(
                    select(Warehouse).where(Warehouse.merchant_id == merchant_id).with_for_update()
                    .execution_options(populate_existing=True)
                )
                if warehouse is None:
                    raise InventoryConflict("merchant warehouse is not configured")
                warehouse_id = warehouse.id
                spu = session.scalar(
                    select(Spu).where(Spu.id == parent_spu_id, Spu.merchant_id == merchant_id)
                    .with_for_update().execution_options(populate_existing=True)
                )
                if spu is None:
                    raise InventoryNotFound("SKU not found")
                sku = session.scalar(
                    select(Sku).where(Sku.id == sku_id, Sku.spu_id == spu.id, Sku.merchant_id == merchant_id)
                    .with_for_update().execution_options(populate_existing=True)
                )
                if sku is None:
                    raise InventoryNotFound("SKU not found")
                request_hash = _request_hash(
                    actor_user_id=actor_user_id, merchant_id=merchant_id,
                    warehouse_id=warehouse_id, sku_id=sku_id, delta=delta,
                )
                existing = session.scalar(select(StockMovement).where(
                    StockMovement.merchant_id == merchant_id,
                    StockMovement.idempotency_key == key,
                ))
                if existing is not None:
                    if existing.request_hash != request_hash:
                        raise InventoryConflict("Idempotency-Key was already used for a different request")
                    response = _operation_response(existing)
                    replay = True
                    # Returning after the context exits commits no changes and
                    # preserves the first result even if current stock has moved.
                    movement_id = existing.id
                else:
                    stock = lock_stock_row(
                        session, merchant_id=merchant_id, warehouse_id=warehouse_id, sku_id=sku_id
                    )
                    before = int(stock.quantity)
                    after = checked_quantity_after(before, delta)
                    movement = StockMovement(
                        merchant_id=merchant_id, warehouse_id=warehouse_id, sku_id=sku_id,
                        actor_user_id=actor_user_id, operation_type="MANUAL_ADJUSTMENT",
                        quantity_delta=delta, quantity_before=before, quantity_after=after,
                        idempotency_key=key, request_hash=request_hash,
                    )
                    session.add(movement)
                    # Insert first so an idempotency uniqueness failure happens before
                    # quantity changes. Both still commit in this one transaction.
                    session.flush()
                    movement_id = movement.id
                    stock.quantity = after
                    session.flush()
                    session.refresh(movement)
                    response = _operation_response(movement)
        except IntegrityError as exc:
            if not _duplicate(exc):
                raise
            # The failed transaction has been rolled back by Session.begin().
            # Resolve the unique-key winner through a fresh Session, never through
            # the failed transaction's stale identity map/snapshot.
            from app.db.session import get_session_factory

            with get_session_factory()() as lookup:
                existing = lookup.scalar(select(StockMovement).where(
                    StockMovement.merchant_id == merchant_id,
                    StockMovement.idempotency_key == key,
                ))
                if existing is None:
                    raise
                attempted_hash = _request_hash(
                    actor_user_id=actor_user_id, merchant_id=merchant_id,
                    warehouse_id=warehouse_id, sku_id=sku_id, delta=delta,
                )
                if existing.request_hash != attempted_hash:
                    raise InventoryConflict("Idempotency-Key was already used for a different request") from exc
                return _operation_response(existing)
        if replay:
            return response
        audit_writer(
            actor_user_id=actor_user_id, event_type="inventory_stock_adjusted",
            target_type="stock_movement", target_id=movement_id, outcome="ADJUSTED",
            event_data={"merchant_id": merchant_id, "warehouse_id": warehouse_id,
                        "sku_id": sku_id, "movement_id": movement_id},
        )
        return response

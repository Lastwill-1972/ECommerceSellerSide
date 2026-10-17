from datetime import datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.modules.catalog.models import Category, ProductOffshelfApplication, Sku, Spu
from app.modules.employees.models import EmployeeApplication, ShopEmployee, ShopEmployeeRole
from app.modules.inventory.models import StockMovement, WarehouseAccessApplication
from app.modules.merchants.models import AuditEvent, MerchantApplication, Merchant
from app.modules.orders.models import Order
from app.modules.shops.models import Shop, ShopApplication
from app.core.audit_schemas import AuditEventItem, AuditEventPage
from app.services.identity import Identity, Role


def _event(target_type: str, event_types: tuple[str, ...], target_id):
    return and_(AuditEvent.target_type == target_type, AuditEvent.event_type.in_(event_types), AuditEvent.target_id == target_id)


def _scope(identity: Identity):
    if identity.role is Role.PLATFORM_ADMIN:
        # Platform audit access follows platform review duties only.
        return or_(
            _event("merchant_application", ("merchant_application_submitted", "merchant_application_reviewed"),
                   select(MerchantApplication.id).where(MerchantApplication.id == AuditEvent.target_id).scalar_subquery()),
            and_(
                _event("product_offshelf_application", ("catalog_offshelf_application_reviewed",),
                       select(ProductOffshelfApplication.id).where(
                           ProductOffshelfApplication.id == AuditEvent.target_id,
                           ProductOffshelfApplication.reviewer_user_id == AuditEvent.actor_user_id,
                       ).scalar_subquery()),
            ),
        )
    if identity.role is Role.MERCHANT_ADMIN and identity.merchant_id is not None:
        merchant_id = identity.merchant_id
        merchant_scope = or_(
            and_(AuditEvent.event_type == "merchant_profile_updated", AuditEvent.target_type == "merchant",
                 AuditEvent.target_id == merchant_id),
            and_(AuditEvent.event_type.in_(("shop_owner_transfer_proposed", "shop_owner_transfer_confirmed", "shop_owner_transfer_forced")),
                 AuditEvent.target_type == "shop", AuditEvent.target_id.in_(select(Shop.id).where(Shop.merchant_id == merchant_id))),
            and_(AuditEvent.event_type.in_(("shop_application_submitted", "shop_application_reviewed")),
                 AuditEvent.target_type == "shop_application", AuditEvent.target_id.in_(
                     select(ShopApplication.id).where(ShopApplication.merchant_id == merchant_id))),
            and_(AuditEvent.event_type.in_(("merchant_application_submitted", "merchant_application_reviewed")),
                 AuditEvent.target_type == "merchant_application", AuditEvent.target_id.in_(
                     select(MerchantApplication.id).where(MerchantApplication.created_merchant_id == merchant_id))),
            and_(AuditEvent.event_type.in_(("employee_application_submitted", "employee_application_reviewed")),
                 AuditEvent.target_type == "employee_application", AuditEvent.target_id.in_(
                     select(EmployeeApplication.id).join(Shop, Shop.id == EmployeeApplication.shop_id)
                     .where(Shop.merchant_id == merchant_id))),
            and_(AuditEvent.event_type == "shop_employee_role_created", AuditEvent.target_type == "shop_employee_role",
                 AuditEvent.target_id.in_(select(ShopEmployeeRole.id).join(Shop, Shop.id == ShopEmployeeRole.shop_id)
                                          .where(Shop.merchant_id == merchant_id))),
            and_(AuditEvent.event_type == "shop_employee_role_changed", AuditEvent.target_type == "shop_employee",
                 AuditEvent.target_id.in_(select(ShopEmployee.id).join(Shop, Shop.id == ShopEmployee.shop_id)
                                          .where(Shop.merchant_id == merchant_id))),
            and_(AuditEvent.event_type.in_(("catalog_category_created",)), AuditEvent.target_type == "category",
                 AuditEvent.target_id.in_(select(Category.id).where(Category.merchant_id == merchant_id))),
            and_(AuditEvent.event_type.in_(("catalog_spu_created", "catalog_spu_updated", "catalog_spu_published")),
                 AuditEvent.target_type == "spu", AuditEvent.target_id.in_(select(Spu.id).where(Spu.merchant_id == merchant_id))),
            and_(AuditEvent.event_type.in_(("catalog_sku_created", "catalog_sku_updated")), AuditEvent.target_type == "sku",
                 AuditEvent.target_id.in_(select(Sku.id).join(Spu, Spu.id == Sku.spu_id).where(Spu.merchant_id == merchant_id))),
            and_(AuditEvent.event_type.in_(("catalog_offshelf_application_submitted", "catalog_offshelf_application_reviewed")),
                 AuditEvent.target_type == "product_offshelf_application", AuditEvent.target_id.in_(
                     select(ProductOffshelfApplication.id).join(Spu, Spu.id == ProductOffshelfApplication.spu_id)
                     .where(Spu.merchant_id == merchant_id))),
            and_(AuditEvent.event_type.in_(("warehouse_access_application_submitted", "warehouse_access_application_reviewed")),
                 AuditEvent.target_type == "warehouse_access_application", AuditEvent.target_id.in_(
                     select(WarehouseAccessApplication.id).join(Shop, Shop.id == WarehouseAccessApplication.shop_id)
                     .where(Shop.merchant_id == merchant_id))),
            and_(AuditEvent.event_type == "inventory_stock_adjusted", AuditEvent.target_type == "stock_movement",
                 AuditEvent.target_id.in_(select(StockMovement.id).where(StockMovement.merchant_id == merchant_id))),
            and_(AuditEvent.event_type.in_(("order_created", "order_completed", "order_cancelled")), AuditEvent.target_type == "order",
                 AuditEvent.target_id.in_(select(Order.id).where(Order.merchant_id == merchant_id))),
        )
        return merchant_scope
    if identity.role is Role.SHOP_OWNER and identity.shop_id is not None:
        shop_id = identity.shop_id
        return or_(
            and_(AuditEvent.event_type.in_(("shop_owner_transfer_proposed", "shop_owner_transfer_confirmed", "shop_owner_transfer_forced")),
                 AuditEvent.target_type == "shop", AuditEvent.target_id == shop_id,
                 AuditEvent.target_id.in_(select(Shop.id).where(Shop.owner_user_id == identity.user_id))),
            and_(AuditEvent.event_type.in_(("employee_application_submitted", "employee_application_reviewed")),
                 AuditEvent.target_type == "employee_application", AuditEvent.target_id.in_(
                     select(EmployeeApplication.id).where(EmployeeApplication.shop_id == shop_id))),
            and_(AuditEvent.event_type == "shop_employee_role_created", AuditEvent.target_type == "shop_employee_role",
                 AuditEvent.target_id.in_(select(ShopEmployeeRole.id).where(ShopEmployeeRole.shop_id == shop_id))),
            and_(AuditEvent.event_type == "shop_employee_role_changed", AuditEvent.target_type == "shop_employee",
                 AuditEvent.target_id.in_(select(ShopEmployee.id).where(ShopEmployee.shop_id == shop_id))),
            and_(AuditEvent.event_type.in_(("warehouse_access_application_submitted", "warehouse_access_application_reviewed")),
                 AuditEvent.target_type == "warehouse_access_application", AuditEvent.target_id.in_(
                     select(WarehouseAccessApplication.id).where(WarehouseAccessApplication.shop_id == shop_id))),
            and_(AuditEvent.event_type.in_(("order_created", "order_completed", "order_cancelled")),
                 AuditEvent.target_type == "order", AuditEvent.target_id.in_(select(Order.id).where(Order.shop_id == shop_id))),
        )
    return None


class AuditQueryService:
    @staticmethod
    def list_events(session: Session, *, identity: Identity, limit: int, offset: int,
                    event_type: str | None, start_at: datetime | None, end_at: datetime | None,
                    target_type: str | None) -> AuditEventPage:
        scope = _scope(identity)
        if scope is None:
            from app.core.permissions import AccessDenied
            raise AccessDenied("audit query is not available for this identity")
        conditions = [scope]
        if event_type is not None:
            conditions.append(AuditEvent.event_type == event_type)
        if start_at is not None:
            conditions.append(AuditEvent.created_at >= start_at)
        if end_at is not None:
            conditions.append(AuditEvent.created_at <= end_at)
        if target_type is not None:
            conditions.append(AuditEvent.target_type == target_type)
        total = session.scalar(select(func.count(AuditEvent.id)).where(*conditions)) or 0
        rows = session.scalars(select(AuditEvent).where(*conditions)
                               .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
                               .limit(limit).offset(offset)).all()
        items = []
        for row in rows:
            safe = {}
            # Only decisions can be safely reconstructed from durable business state.
            # Identifiers and free-form JSON values are omitted even when historically whitelisted.
            review_models = {
                "merchant_application_reviewed": (MerchantApplication, "merchant_application"),
                "shop_application_reviewed": (ShopApplication, "shop_application"),
                "employee_application_reviewed": (EmployeeApplication, "employee_application"),
                "catalog_offshelf_application_reviewed": (ProductOffshelfApplication, "product_offshelf_application"),
                "warehouse_access_application_reviewed": (WarehouseAccessApplication, "warehouse_access_application"),
            }
            review = review_models.get(row.event_type)
            if review is not None and row.target_type == review[1]:
                status = session.scalar(select(review[0].status).where(review[0].id == row.target_id))
                if status in ("APPROVED", "REJECTED") and row.outcome == status:
                    safe = {"decision": "APPROVE" if status == "APPROVED" else "REJECT"}
            items.append(AuditEventItem(id=row.id, actor_user_id=row.actor_user_id, event_type=row.event_type,
                                        target_type=row.target_type, target_id=row.target_id, outcome=row.outcome,
                                        event_data=safe, created_at=row.created_at))
        return AuditEventPage(items=items, limit=limit, offset=offset, total=total)

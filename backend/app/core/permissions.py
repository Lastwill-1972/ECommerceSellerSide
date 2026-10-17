from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.services.identity import Identity, Role
from app.modules.shops.models import Shop


class AccessDenied(Exception):
    pass


class OrderAction(StrEnum):
    READ = "read"
    CREATE = "create"
    COMPLETE = "complete"
    CANCEL = "cancel"


class AuthorizationService:
    """Reusable tenant policy checks using resource IDs loaded from the database."""

    @staticmethod
    def require_platform_admin(identity: Identity) -> None:
        if identity.role is not Role.PLATFORM_ADMIN:
            raise AccessDenied("platform administrator required")

    @staticmethod
    def check_merchant(identity: Identity, merchant_id: int) -> None:
        if identity.role is not Role.MERCHANT_ADMIN or identity.merchant_id != merchant_id:
            raise AccessDenied("merchant administrator for this merchant required")

    @staticmethod
    def check_merchant_management(identity: Identity, merchant_id: int) -> None:
        AuthorizationService.check_merchant(identity, merchant_id)

    @staticmethod
    def catalog_merchant(identity: Identity, *, write: bool) -> int:
        """Return the database-derived catalog tenant for the fixed catalog policy."""
        allowed = (Role.MERCHANT_ADMIN,) if write else (
            Role.MERCHANT_ADMIN, Role.SHOP_OWNER, Role.SHOP_EMPLOYEE
        )
        if identity.role not in allowed or identity.merchant_id is None:
            raise AccessDenied("user has no catalog access")
        return identity.merchant_id

    @staticmethod
    def check_shop(session: Session, identity: Identity, shop_id: int) -> int:
        row = session.execute(select(Shop.id, Shop.merchant_id).where(Shop.id == shop_id)).one_or_none()
        if row is None:
            raise AccessDenied("shop does not exist")
        if identity.role is Role.SHOP_OWNER and identity.shop_id == row.id:
            return row.merchant_id
        if identity.role is Role.SHOP_EMPLOYEE and identity.shop_id == row.id:
            return row.merchant_id
        if identity.role is Role.MERCHANT_ADMIN and identity.merchant_id == row.merchant_id:
            return row.merchant_id
        raise AccessDenied("user has no access to this shop")

    @staticmethod
    def check_shop_management(session: Session, identity: Identity, shop_id: int) -> int:
        merchant_id = AuthorizationService.check_shop(session, identity, shop_id)
        if identity.role is not Role.SHOP_OWNER:
            raise AccessDenied("shop owner required")
        return merchant_id

    @staticmethod
    def check_order_action(session: Session, identity: Identity, *, shop_id: int, action: OrderAction) -> int:
        try:
            action = OrderAction(action)
        except (TypeError, ValueError) as exc:
            raise AccessDenied("unsupported order action") from exc
        merchant_id = AuthorizationService.check_shop(session, identity, shop_id)
        if identity.role is Role.MERCHANT_ADMIN and action is not OrderAction.READ:
            raise AccessDenied("merchant administrators have read-only order access")
        if identity.role not in (Role.MERCHANT_ADMIN, Role.SHOP_OWNER, Role.SHOP_EMPLOYEE):
            raise AccessDenied("merchant administrator, shop owner, or shop employee required")
        return merchant_id

    @staticmethod
    def check_resource_merchant(identity: Identity, resource_merchant_id: int) -> None:
        AuthorizationService.check_merchant(identity, resource_merchant_id)

    @staticmethod
    def merchant_for_shop(session: Session, shop_id: int) -> int:
        merchant_id = session.scalar(select(Shop.merchant_id).where(Shop.id == shop_id))
        if merchant_id is None:
            raise AccessDenied("shop does not exist")
        return merchant_id

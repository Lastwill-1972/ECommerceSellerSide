from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.merchants.models import Merchant
from app.modules.employees.models import ShopEmployee
from app.modules.shops.models import Shop
from app.modules.users.models import User


class Role(StrEnum):
    PLATFORM_ADMIN = "PLATFORM_ADMIN"
    MERCHANT_ADMIN = "MERCHANT_ADMIN"
    SHOP_OWNER = "SHOP_OWNER"
    SHOP_EMPLOYEE = "SHOP_EMPLOYEE"
    NONE = "NONE"


class IdentityConflict(Exception):
    """A user has multiple mutually exclusive platform or tenant identities."""


class IdentityEligibilityError(Exception):
    """A user cannot be assigned an identity under the current relationship state."""


@dataclass(frozen=True, slots=True)
class Identity:
    user_id: int
    role: Role
    merchant_id: int | None = None
    shop_id: int | None = None


class IdentityService:
    @staticmethod
    def get_identity(session: Session, user: User) -> Identity:
        if not user.is_active:
            raise IdentityEligibilityError("inactive users have no usable identity")
        merchant_id = session.scalar(select(Merchant.id).where(Merchant.admin_user_id == user.id))
        shop_row = session.execute(
            select(Shop.id, Shop.merchant_id).where(Shop.owner_user_id == user.id)
        ).one_or_none()
        employee_row = session.execute(
            select(ShopEmployee.shop_id, Shop.merchant_id)
            .join(Shop, Shop.id == ShopEmployee.shop_id)
            .where(ShopEmployee.user_id == user.id)
        ).one_or_none()
        has_platform_role = bool(user.is_platform_admin)
        identities = (
            int(has_platform_role)
            + int(merchant_id is not None)
            + int(shop_row is not None)
            + int(employee_row is not None)
        )
        if identities > 1:
            raise IdentityConflict("conflicting platform and tenant identity relationships")
        if has_platform_role:
            return Identity(user.id, Role.PLATFORM_ADMIN)
        if merchant_id is not None:
            return Identity(user.id, Role.MERCHANT_ADMIN, merchant_id=merchant_id)
        if shop_row is not None:
            return Identity(user.id, Role.SHOP_OWNER, merchant_id=shop_row.merchant_id, shop_id=shop_row.id)
        if employee_row is not None:
            return Identity(
                user.id,
                Role.SHOP_EMPLOYEE,
                merchant_id=employee_row.merchant_id,
                shop_id=employee_row.shop_id,
            )
        return Identity(user.id, Role.NONE)

    @classmethod
    def ensure_eligible_for_merchant_admin(cls, session: Session, user_id: int) -> User:
        return cls._ensure_none_identity(session, user_id)

    @classmethod
    def ensure_eligible_for_shop_owner(cls, session: Session, user_id: int) -> User:
        return cls._ensure_none_identity(session, user_id)

    @classmethod
    def ensure_eligible_for_shop_employee(cls, session: Session, user_id: int) -> User:
        return cls._ensure_none_identity(session, user_id)

    @classmethod
    def _ensure_none_identity(cls, session: Session, user_id: int) -> User:
        user = session.scalar(select(User).where(User.id == user_id))
        if user is None or not user.is_active:
            raise IdentityEligibilityError("an active existing user is required")
        identity = cls.get_identity(session, user)
        if identity.role is not Role.NONE:
            raise IdentityEligibilityError("user already has a platform or tenant identity")
        # Callers assigning an identity must lock this user's root row first, then
        # re-check this relationship inside the same transaction.
        return user

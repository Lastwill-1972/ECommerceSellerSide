import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.merchants.models import Merchant
from app.modules.users.models import User
from app.services.audit import AuditService
from app.services.identity import IdentityConflict, IdentityEligibilityError, IdentityService, Role

logger = logging.getLogger(__name__)


class MerchantProfileForbidden(Exception):
    pass


class MerchantProfileConflict(Exception):
    pass


class MerchantProfileService:
    @staticmethod
    def update_name(session: Session, *, actor_user_id: int, merchant_id: int, name: str,
                    audit_writer=AuditService.record_after_commit):
        if session.in_transaction():
            raise RuntimeError("merchant profile write requires a fresh transaction")
        with session.begin():
            user = session.scalar(select(User).where(User.id == actor_user_id).with_for_update()
                                  .execution_options(populate_existing=True))
            if user is None or not user.is_active:
                raise MerchantProfileForbidden("active merchant administrator required")
            try:
                identity = IdentityService.get_identity(session, user)
            except (IdentityConflict, IdentityEligibilityError) as exc:
                raise MerchantProfileForbidden("current merchant administrator required") from exc
            if identity.role is not Role.MERCHANT_ADMIN or identity.merchant_id != merchant_id:
                raise MerchantProfileForbidden("current merchant administrator required")
            merchant = session.scalar(select(Merchant).where(Merchant.id == merchant_id).with_for_update()
                                      .execution_options(populate_existing=True))
            if merchant is None or merchant.admin_user_id != actor_user_id:
                raise MerchantProfileForbidden("current merchant administrator required")
            merchant.name = name
            session.flush()
            result = {"id": merchant.id, "name": merchant.name}
        try:
            audit_writer(actor_user_id=actor_user_id, event_type="merchant_profile_updated", target_type="merchant",
                         target_id=merchant_id, outcome="UPDATED", event_data={})
        except Exception as exc:
            # AuditService is intentionally independent and must not reverse committed profile changes.
            logger.error("merchant profile audit failed event_type=merchant_profile_updated target_id=%s error_type=%s",
                         merchant_id, type(exc).__name__)
        return result

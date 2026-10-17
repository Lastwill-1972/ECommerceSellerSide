from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.inventory.models import Warehouse, WarehouseAccess, WarehouseAccessApplication
from app.modules.inventory.schemas import (
    ApplicationDecision, AccessApplicationPage, AccessApplicationResponse, ApplicationStatus,
)
from app.modules.merchants.models import Merchant
from app.modules.shops.models import Shop
from app.modules.users.models import User
from app.services.audit import AuditService
from app.services.identity import IdentityConflict, IdentityEligibilityError, IdentityService, Role


class WarehouseAccessConflict(Exception):
    pass


class WarehouseAccessForbidden(Exception):
    pass


class WarehouseAccessNotFound(Exception):
    pass


class WarehouseAccessIneligible(Exception):
    pass


def _require_fresh_session(session: Session) -> None:
    if session.in_transaction():
        raise RuntimeError("write service requires a fresh transaction boundary")


def _lock_users(session: Session, ids: set[int]) -> dict[int, User]:
    users = session.scalars(
        select(User).where(User.id.in_(sorted(ids))).order_by(User.id).with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    result = {user.id: user for user in users}
    if len(result) != len(ids):
        raise WarehouseAccessIneligible("required active user does not exist")
    return result


def _response(row: WarehouseAccessApplication) -> AccessApplicationResponse:
    return AccessApplicationResponse.model_validate(row, from_attributes=True)


class WarehouseAccessService:
    @staticmethod
    def prepare_review(session: Session, application_id: int) -> tuple[int, int]:
        row = session.execute(
            select(WarehouseAccessApplication.applicant_user_id, WarehouseAccessApplication.shop_id)
            .where(WarehouseAccessApplication.id == application_id)
        ).one_or_none()
        if row is None:
            raise WarehouseAccessNotFound("application not found")
        return int(row.applicant_user_id), int(row.shop_id)

    @staticmethod
    def end_read_only_transaction(session: Session) -> None:
        if session.new or session.dirty or session.deleted:
            raise RuntimeError("cannot discard a Session containing pending changes")
        if session.in_transaction():
            session.rollback()

    @staticmethod
    def submit(session: Session, *, applicant_user_id: int, audit_writer=AuditService.record_after_commit):
        _require_fresh_session(session)
        try:
            with session.begin():
                users = _lock_users(session, {applicant_user_id})
                applicant = users[applicant_user_id]
                if not applicant.is_active:
                    raise WarehouseAccessIneligible("active shop owner required")
                try:
                    identity = IdentityService.get_identity(session, applicant)
                except (IdentityConflict, IdentityEligibilityError) as exc:
                    raise WarehouseAccessForbidden("current shop owner identity required") from exc
                if identity.role is not Role.SHOP_OWNER or identity.shop_id is None or identity.merchant_id is None:
                    raise WarehouseAccessForbidden("current shop owner identity required")

                shop = session.scalar(
                    select(Shop).where(Shop.id == identity.shop_id).with_for_update()
                    .execution_options(populate_existing=True)
                )
                if shop is None or shop.owner_user_id != applicant_user_id:
                    raise WarehouseAccessForbidden("current shop owner identity required")
                warehouse = session.scalar(
                    select(Warehouse).where(Warehouse.merchant_id == shop.merchant_id).with_for_update()
                    .execution_options(populate_existing=True)
                )
                if warehouse is None:
                    raise WarehouseAccessConflict("merchant warehouse is not configured")
                existing_access = session.scalar(
                    select(WarehouseAccess.id).where(WarehouseAccess.shop_id == shop.id).with_for_update()
                )
                if existing_access is not None:
                    raise WarehouseAccessConflict("shop already has warehouse access")
                pending_id = session.scalar(
                    select(WarehouseAccessApplication.id)
                    .where(WarehouseAccessApplication.shop_id == shop.id,
                           WarehouseAccessApplication.status == ApplicationStatus.PENDING.value)
                    .limit(1)
                )
                if pending_id is not None:
                    raise WarehouseAccessConflict("an access application is already pending")
                application = WarehouseAccessApplication(
                    shop_id=shop.id, applicant_user_id=applicant_user_id, status=ApplicationStatus.PENDING.value
                )
                session.add(application)
                session.flush()
                session.refresh(application)
                response = _response(application)
                application_id, shop_id, warehouse_id = application.id, shop.id, warehouse.id
        except IntegrityError as exc:
            if getattr(getattr(exc, "orig", None), "args", [None])[0] == 1062:
                raise WarehouseAccessConflict("an access application or warehouse grant already exists") from exc
            raise
        audit_writer(
            actor_user_id=applicant_user_id,
            event_type="warehouse_access_application_submitted",
            target_type="warehouse_access_application", target_id=application_id, outcome="PENDING",
            event_data={"shop_id": shop_id, "warehouse_id": warehouse_id, "applicant_user_id": applicant_user_id},
        )
        return response

    @staticmethod
    def list_applications(session: Session, *, actor_user_id: int, role: Role, merchant_id: int | None,
                          shop_id: int | None, status_filter: ApplicationStatus | None,
                          limit: int, offset: int) -> AccessApplicationPage:
        scope = None
        if role is Role.SHOP_OWNER and shop_id is not None:
            scope = WarehouseAccessApplication.shop_id == shop_id
        elif role is Role.MERCHANT_ADMIN and merchant_id is not None:
            scope = WarehouseAccessApplication.shop_id.in_(
                select(Shop.id).where(Shop.merchant_id == merchant_id)
            )
        else:
            raise WarehouseAccessForbidden("warehouse access applications are limited to shop owners and merchant administrators")
        if status_filter is not None:
            scope = scope & (WarehouseAccessApplication.status == status_filter.value)
        total = session.scalar(select(func.count(WarehouseAccessApplication.id)).where(scope)) or 0
        rows = session.scalars(
            select(WarehouseAccessApplication).where(scope)
            .order_by(WarehouseAccessApplication.created_at.desc(), WarehouseAccessApplication.id.desc())
            .limit(limit).offset(offset)
        ).all()
        return AccessApplicationPage(items=[_response(row) for row in rows], limit=limit, offset=offset, total=total)

    @staticmethod
    def review(session: Session, *, reviewer_user_id: int, expected_merchant_id: int,
               application_id: int, expected_applicant_user_id: int, expected_shop_id: int,
               decision: ApplicationDecision, rejection_reason: str | None,
               audit_writer=AuditService.record_after_commit) -> AccessApplicationResponse:
        _require_fresh_session(session)
        with session.begin():
            users = _lock_users(session, {reviewer_user_id, expected_applicant_user_id})
            reviewer = users[reviewer_user_id]
            applicant = users[expected_applicant_user_id]
            if not reviewer.is_active:
                raise WarehouseAccessForbidden("active merchant administrator required")
            try:
                reviewer_identity = IdentityService.get_identity(session, reviewer)
            except (IdentityConflict, IdentityEligibilityError) as exc:
                raise WarehouseAccessForbidden("current merchant administrator required") from exc
            if reviewer_identity.role is not Role.MERCHANT_ADMIN or reviewer_identity.merchant_id != expected_merchant_id:
                raise WarehouseAccessForbidden("current merchant administrator required")

            shop = session.scalar(
                select(Shop).where(Shop.id == expected_shop_id).with_for_update()
                .execution_options(populate_existing=True)
            )
            if shop is None or shop.merchant_id != expected_merchant_id:
                raise WarehouseAccessNotFound("application not found")
            warehouse = session.scalar(
                select(Warehouse).where(Warehouse.merchant_id == expected_merchant_id).with_for_update()
                .execution_options(populate_existing=True)
            )
            if warehouse is None:
                raise WarehouseAccessConflict("merchant warehouse is not configured")
            application = session.scalar(
                select(WarehouseAccessApplication)
                .where(WarehouseAccessApplication.id == application_id,
                       WarehouseAccessApplication.shop_id == shop.id)
                .with_for_update().execution_options(populate_existing=True)
            )
            if application is None or application.applicant_user_id != expected_applicant_user_id:
                raise WarehouseAccessNotFound("application not found")
            if application.status != ApplicationStatus.PENDING.value:
                raise WarehouseAccessConflict("application is already reviewed")
            if application.applicant_user_id == reviewer_user_id:
                raise WarehouseAccessForbidden("applicant cannot review own application")

            if decision is ApplicationDecision.APPROVE:
                if not applicant.is_active:
                    raise WarehouseAccessConflict("applicant is no longer an active shop owner")
                try:
                    applicant_identity = IdentityService.get_identity(session, applicant)
                except (IdentityConflict, IdentityEligibilityError) as exc:
                    raise WarehouseAccessConflict("applicant is no longer the shop owner") from exc
                if applicant_identity.role is not Role.SHOP_OWNER or applicant_identity.shop_id != shop.id:
                    raise WarehouseAccessConflict("applicant is no longer the shop owner")
                if shop.owner_user_id != expected_applicant_user_id:
                    raise WarehouseAccessConflict("applicant is no longer the shop owner")
                if reviewer_identity.merchant_id != shop.merchant_id:
                    raise WarehouseAccessNotFound("application not found")
                existing = session.scalar(
                    select(WarehouseAccess.id).where(WarehouseAccess.shop_id == shop.id).with_for_update()
                )
                if existing is not None:
                    raise WarehouseAccessConflict("shop already has warehouse access")
                access = WarehouseAccess(
                    shop_id=shop.id, warehouse_id=warehouse.id, merchant_id=shop.merchant_id,
                    granted_by_user_id=reviewer_user_id,
                )
                session.add(access)
                application.status = ApplicationStatus.APPROVED.value
                application.reviewer_user_id = reviewer_user_id
                application.rejection_reason = None
                application.reviewed_at = func.current_timestamp()
            else:
                application.status = ApplicationStatus.REJECTED.value
                application.reviewer_user_id = reviewer_user_id
                application.rejection_reason = rejection_reason
                application.reviewed_at = func.current_timestamp()
            session.flush()
            session.refresh(application)
            response = _response(application)
            shop_id, warehouse_id, decision_value = shop.id, warehouse.id, decision.value

        audit_writer(
            actor_user_id=reviewer_user_id,
            event_type="warehouse_access_application_reviewed",
            target_type="warehouse_access_application", target_id=application_id,
            outcome=ApplicationStatus.APPROVED.value if decision_value == "APPROVE" else ApplicationStatus.REJECTED.value,
            event_data={"shop_id": shop_id, "warehouse_id": warehouse_id,
                        "reviewer_user_id": reviewer_user_id, "decision": decision_value},
        )
        return response


def is_authorized(session: Session, shop_id: int) -> bool:
    """Check the durable, tenant-consistent grant; caller role is irrelevant."""
    return session.scalar(
        select(WarehouseAccess.id)
        .join(Shop, (Shop.id == WarehouseAccess.shop_id) & (Shop.merchant_id == WarehouseAccess.merchant_id))
        .join(Warehouse, (Warehouse.id == WarehouseAccess.warehouse_id) &
              (Warehouse.merchant_id == WarehouseAccess.merchant_id))
        .where(WarehouseAccess.shop_id == shop_id)
        .limit(1)
    ) is not None

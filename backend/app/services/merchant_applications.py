from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.inventory.models import Warehouse
from app.modules.merchants.models import Merchant, MerchantApplication
from app.modules.merchants.schemas import (
    ApplicationDecision,
    ApplicationStatus,
    MerchantApplicationPage,
    MerchantApplicationResponse,
)
from app.modules.users.models import User
from app.services.audit import AuditService
from app.services.identity import IdentityConflict, IdentityService, Role


class ApplicationConflict(Exception):
    pass


class ApplicationNotFound(Exception):
    pass


class ApplicationForbidden(Exception):
    pass


class ApplicationIneligible(Exception):
    pass


def _require_fresh_session(session: Session) -> None:
    if session.in_transaction():
        raise RuntimeError("write service requires a fresh transaction boundary")


def _lock_users_in_order(session: Session, user_ids: set[int]) -> dict[int, User]:
    """Lock identity roots before relationship reads, in stable ascending order."""
    users = session.scalars(
        select(User)
        .where(User.id.in_(sorted(user_ids)))
        .order_by(User.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    result = {user.id: user for user in users}
    if len(result) != len(user_ids):
        raise ApplicationIneligible("required active user does not exist")
    return result


def _response(application: MerchantApplication) -> MerchantApplicationResponse:
    return MerchantApplicationResponse.model_validate(application, from_attributes=True)


class MerchantApplicationService:
    @staticmethod
    def prepare_review_applicant(session: Session, application_id: int) -> int:
        """Read the immutable applicant ID before starting the write transaction."""
        applicant_user_id = session.scalar(
            select(MerchantApplication.applicant_user_id).where(MerchantApplication.id == application_id)
        )
        if applicant_user_id is None:
            raise ApplicationNotFound("application not found")
        return int(applicant_user_id)

    @staticmethod
    def end_read_only_transaction(session: Session) -> None:
        """Close dependency/auth reads before the top-level service begins writing."""
        if session.new or session.dirty or session.deleted:
            raise RuntimeError("cannot discard a Session containing pending changes")
        if session.in_transaction():
            session.rollback()

    @staticmethod
    def list_applications(
        session: Session,
        *,
        actor_user_id: int,
        platform_admin: bool,
        status_filter: ApplicationStatus | None,
        limit: int,
        offset: int,
    ) -> MerchantApplicationPage:
        query = select(MerchantApplication)
        count_query = select(func.count(MerchantApplication.id))
        if platform_admin:
            if status_filter not in (None, ApplicationStatus.PENDING):
                raise ApplicationForbidden("platform review listing is limited to pending applications")
            scope = MerchantApplication.status == ApplicationStatus.PENDING.value
        else:
            scope = MerchantApplication.applicant_user_id == actor_user_id
            if status_filter is not None:
                scope = scope & (MerchantApplication.status == status_filter.value)
        query = query.where(scope).order_by(MerchantApplication.created_at.desc(), MerchantApplication.id.desc())
        count_query = count_query.where(scope)
        total = session.scalar(count_query) or 0
        applications = session.scalars(query.limit(limit).offset(offset)).all()
        return MerchantApplicationPage(
            items=[_response(application) for application in applications],
            limit=limit,
            offset=offset,
            total=total,
        )

    @staticmethod
    def submit(
        session: Session,
        *,
        applicant_user_id: int,
        merchant_name: str,
        audit_writer=AuditService.record_after_commit,
    ) -> MerchantApplicationResponse:
        _require_fresh_session(session)
        with session.begin():
            users = _lock_users_in_order(session, {applicant_user_id})
            applicant = users[applicant_user_id]
            if not applicant.is_active:
                raise ApplicationIneligible("active user required")
            try:
                identity = IdentityService.get_identity(session, applicant)
            except IdentityConflict as exc:
                raise ApplicationIneligible("applicant has conflicting identity relationships") from exc
            if identity.role is not Role.NONE:
                raise ApplicationIneligible("applicant must have no existing platform or tenant identity")

            pending = session.scalar(
                select(MerchantApplication.id)
                .where(
                    MerchantApplication.applicant_user_id == applicant_user_id,
                    MerchantApplication.status == ApplicationStatus.PENDING.value,
                )
                .limit(1)
            )
            if pending is not None:
                raise ApplicationConflict("an application is already pending")

            application = MerchantApplication(
                applicant_user_id=applicant_user_id,
                merchant_name=merchant_name,
                status=ApplicationStatus.PENDING.value,
            )
            session.add(application)
            session.flush()
            session.refresh(application)
            response = _response(application)
            application_id = application.id

        audit_writer(
            actor_user_id=applicant_user_id,
            event_type="merchant_application_submitted",
            target_type="merchant_application",
            target_id=application_id,
            outcome="PENDING",
            event_data={},
        )
        return response

    @staticmethod
    def review(
        session: Session,
        *,
        reviewer_user_id: int,
        application_id: int,
        expected_applicant_user_id: int,
        decision: ApplicationDecision,
        rejection_reason: str | None,
        audit_writer=AuditService.record_after_commit,
    ) -> MerchantApplicationResponse:
        _require_fresh_session(session)
        with session.begin():
            users = _lock_users_in_order(
                session,
                {reviewer_user_id, expected_applicant_user_id},
            )
            reviewer = users[reviewer_user_id]
            applicant = users[expected_applicant_user_id]
            if not reviewer.is_active:
                raise ApplicationIneligible("reviewer must be active")

            application = session.scalar(
                select(MerchantApplication)
                .where(MerchantApplication.id == application_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if application is None:
                raise ApplicationNotFound("application not found")
            if application.applicant_user_id != expected_applicant_user_id:
                raise ApplicationConflict("application applicant changed during review")
            if application.status != ApplicationStatus.PENDING.value:
                raise ApplicationConflict("application has already been reviewed")
            if reviewer_user_id == expected_applicant_user_id:
                raise ApplicationForbidden("applicants cannot review their own applications")

            try:
                reviewer_identity = IdentityService.get_identity(session, reviewer)
            except IdentityConflict as exc:
                raise ApplicationIneligible("reviewer has conflicting identity relationships") from exc
            if reviewer_identity.role is not Role.PLATFORM_ADMIN:
                raise ApplicationForbidden("active platform administrator required")

            if decision is ApplicationDecision.APPROVE:
                if not applicant.is_active:
                    raise ApplicationIneligible("applicant must be active")
                try:
                    applicant_identity = IdentityService.get_identity(session, applicant)
                except IdentityConflict as exc:
                    raise ApplicationIneligible("applicant has conflicting identity relationships") from exc
                if applicant_identity.role is not Role.NONE:
                    raise ApplicationIneligible("applicant must have no existing platform or tenant identity")
                merchant = _create_merchant(session, application.merchant_name, applicant)
                session.flush()
                _create_default_warehouse(session, merchant)
                application.status = ApplicationStatus.APPROVED.value
                application.created_merchant_id = merchant.id
                application.rejection_reason = None
            else:
                application.status = ApplicationStatus.REJECTED.value
                application.rejection_reason = rejection_reason.strip() if rejection_reason else None
                application.created_merchant_id = None
            application.reviewer_user_id = reviewer_user_id
            application.reviewed_at = func.current_timestamp()
            session.flush()
            session.refresh(application)
            response = _response(application)
            outcome = application.status
            target_id = application.id

        audit_writer(
            actor_user_id=reviewer_user_id,
            event_type="merchant_application_reviewed",
            target_type="merchant_application",
            target_id=target_id,
            outcome=outcome,
            event_data={"decision": decision.value},
        )
        return response


def _create_merchant(session: Session, name: str, applicant: User) -> Merchant:
    merchant = Merchant(name=name, admin_user=applicant)
    session.add(merchant)
    return merchant


def _create_default_warehouse(session: Session, merchant: Merchant) -> Warehouse:
    warehouse = Warehouse(merchant_id=merchant.id, name="Default warehouse")
    session.add(warehouse)
    return warehouse

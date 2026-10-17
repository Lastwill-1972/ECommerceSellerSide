from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.merchants.models import Merchant
from app.modules.employees.models import ShopEmployeeRole
from app.modules.shops.models import Shop, ShopApplication
from app.modules.shops.schemas import (
    ShopApplicationDecision,
    ShopApplicationPage,
    ShopApplicationResponse,
    ShopApplicationStatus,
    ShopPage,
    ShopResponse,
)
from app.modules.users.models import User
from app.services.audit import AuditService
from app.services.identity import IdentityConflict, IdentityService, Role


class ShopApplicationConflict(Exception):
    pass


class ShopApplicationNotFound(Exception):
    pass


class ShopApplicationForbidden(Exception):
    pass


class ShopApplicationIneligible(Exception):
    pass


def _require_fresh_session(session: Session) -> None:
    if session.in_transaction():
        raise RuntimeError("write service requires a fresh transaction boundary")


def _lock_users_in_order(session: Session, user_ids: set[int]) -> dict[int, User]:
    users = session.scalars(
        select(User)
        .where(User.id.in_(sorted(user_ids)))
        .order_by(User.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    result = {user.id: user for user in users}
    if len(result) != len(user_ids):
        raise ShopApplicationIneligible("required active user does not exist")
    return result


def _response(application: ShopApplication) -> ShopApplicationResponse:
    return ShopApplicationResponse.model_validate(application, from_attributes=True)


class ShopApplicationService:
    @staticmethod
    def prepare_submission(session: Session, merchant_id: int) -> int:
        admin_user_id = session.scalar(select(Merchant.admin_user_id).where(Merchant.id == merchant_id))
        if admin_user_id is None:
            raise ShopApplicationNotFound("merchant not found")
        return int(admin_user_id)

    @staticmethod
    def prepare_review(session: Session, application_id: int) -> tuple[int, int, int]:
        row = session.execute(
            select(ShopApplication.applicant_user_id, ShopApplication.merchant_id, Merchant.admin_user_id)
            .join(Merchant, Merchant.id == ShopApplication.merchant_id)
            .where(ShopApplication.id == application_id)
        ).one_or_none()
        if row is None:
            raise ShopApplicationNotFound("application not found")
        return int(row.applicant_user_id), int(row.merchant_id), int(row.admin_user_id)

    @staticmethod
    def end_read_only_transaction(session: Session) -> None:
        if session.new or session.dirty or session.deleted:
            raise RuntimeError("cannot discard a Session containing pending changes")
        if session.in_transaction():
            session.rollback()

    @staticmethod
    def list_applications(
        session: Session,
        *,
        actor_user_id: int,
        role: Role,
        merchant_id: int | None,
        status_filter: ShopApplicationStatus | None,
        limit: int,
        offset: int,
    ) -> ShopApplicationPage:
        query = select(ShopApplication)
        count_query = select(func.count(ShopApplication.id))
        if role is Role.MERCHANT_ADMIN and merchant_id is not None:
            scope = ShopApplication.merchant_id == merchant_id
        else:
            scope = ShopApplication.applicant_user_id == actor_user_id
        if status_filter is not None:
            scope = scope & (ShopApplication.status == status_filter.value)
        total = session.scalar(count_query.where(scope)) or 0
        rows = session.scalars(
            query.where(scope)
            .order_by(ShopApplication.created_at.desc(), ShopApplication.id.desc())
            .limit(limit)
            .offset(offset)
        ).all()
        return ShopApplicationPage(
            items=[_response(row) for row in rows], limit=limit, offset=offset, total=total
        )

    @staticmethod
    def submit(
        session: Session,
        *,
        applicant_user_id: int,
        merchant_id: int,
        expected_admin_user_id: int,
        shop_name: str,
        audit_writer=AuditService.record_after_commit,
    ) -> ShopApplicationResponse:
        _require_fresh_session(session)
        try:
            with session.begin():
                users = _lock_users_in_order(session, {applicant_user_id, expected_admin_user_id})
                applicant = users[applicant_user_id]
                merchant = session.get(Merchant, merchant_id)
                if merchant is None or merchant.admin_user_id != expected_admin_user_id:
                    raise ShopApplicationConflict("merchant administrator changed; retry the request")
                if not applicant.is_active:
                    raise ShopApplicationIneligible("active applicant required")
                try:
                    identity = IdentityService.get_identity(session, applicant)
                except IdentityConflict as exc:
                    raise ShopApplicationIneligible("conflicting identity relationships") from exc
                if identity.role is not Role.NONE:
                    raise ShopApplicationIneligible("applicant must have no existing platform or tenant identity")
                pending = session.scalar(
                    select(ShopApplication.id)
                    .where(
                        ShopApplication.applicant_user_id == applicant_user_id,
                        ShopApplication.status == ShopApplicationStatus.PENDING.value,
                    )
                    .limit(1)
                )
                if pending is not None:
                    raise ShopApplicationConflict("a shop application is already pending")
                # The merchant administrator user row serializes same-merchant name
                # checks. The shops unique key remains the final concurrency guard.
                existing = session.scalar(
                    select(Shop.id).where(Shop.merchant_id == merchant_id, Shop.name == shop_name).limit(1)
                )
                if existing is not None:
                    raise ShopApplicationConflict("shop name already exists for this merchant")
                application = ShopApplication(
                    merchant_id=merchant_id,
                    applicant_user_id=applicant_user_id,
                    shop_name=shop_name,
                    status=ShopApplicationStatus.PENDING.value,
                )
                session.add(application)
                session.flush()
                session.refresh(application)
                response = _response(application)
                application_id = application.id
        except IntegrityError as exc:
            if _is_duplicate_key(exc):
                raise ShopApplicationConflict("a unique shop ownership or merchant/name constraint was violated") from exc
            raise
        audit_writer(
            actor_user_id=applicant_user_id,
            event_type="shop_application_submitted",
            target_type="shop_application",
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
        expected_merchant_id: int,
        expected_admin_user_id: int,
        decision: ShopApplicationDecision,
        rejection_reason: str | None,
        audit_writer=AuditService.record_after_commit,
    ) -> ShopApplicationResponse:
        _require_fresh_session(session)
        try:
            with session.begin():
                users = _lock_users_in_order(
                    session, {reviewer_user_id, expected_applicant_user_id}
                )
                reviewer = users[reviewer_user_id]
                applicant = users[expected_applicant_user_id]
                if reviewer_user_id != expected_admin_user_id:
                    raise ShopApplicationForbidden("only this merchant administrator may review")
                if not reviewer.is_active:
                    raise ShopApplicationIneligible("reviewer must be active")
                application = session.scalar(
                    select(ShopApplication)
                    .where(ShopApplication.id == application_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if application is None:
                    raise ShopApplicationNotFound("application not found")
                if (
                    application.applicant_user_id != expected_applicant_user_id
                    or application.merchant_id != expected_merchant_id
                ):
                    raise ShopApplicationConflict("application changed during review")
                if application.status != ShopApplicationStatus.PENDING.value:
                    raise ShopApplicationConflict("application has already been reviewed")
                if reviewer_user_id == expected_applicant_user_id:
                    raise ShopApplicationForbidden("applicants cannot review their own applications")
                try:
                    reviewer_identity = IdentityService.get_identity(session, reviewer)
                except IdentityConflict as exc:
                    raise ShopApplicationIneligible("reviewer has conflicting identity relationships") from exc
                if (
                    reviewer_identity.role is not Role.MERCHANT_ADMIN
                    or reviewer_identity.merchant_id != expected_merchant_id
                ):
                    raise ShopApplicationForbidden("merchant administrator for this application required")

                if decision is ShopApplicationDecision.APPROVE:
                    if not applicant.is_active:
                        raise ShopApplicationIneligible("applicant must be active")
                    try:
                        applicant_identity = IdentityService.get_identity(session, applicant)
                    except IdentityConflict as exc:
                        raise ShopApplicationIneligible("applicant has conflicting identity relationships") from exc
                    if applicant_identity.role is not Role.NONE:
                        raise ShopApplicationIneligible("applicant must have no existing platform or tenant identity")
                    merchant = session.get(Merchant, expected_merchant_id)
                    if merchant is None or merchant.admin_user_id != reviewer_user_id:
                        raise ShopApplicationForbidden("reviewer no longer administers this merchant")
                    existing = session.scalar(
                        select(Shop.id)
                        .where(Shop.merchant_id == expected_merchant_id, Shop.name == application.shop_name)
                        .limit(1)
                    )
                    if existing is not None:
                        raise ShopApplicationConflict("shop name already exists for this merchant")
                    shop = Shop(
                        merchant_id=expected_merchant_id,
                        name=application.shop_name,
                        owner_user_id=expected_applicant_user_id,
                    )
                    session.add(shop)
                    session.flush()
                    session.add(ShopEmployeeRole(shop_id=shop.id, name="普通员工"))
                    application.status = ShopApplicationStatus.APPROVED.value
                    application.created_shop_id = shop.id
                    application.rejection_reason = None
                else:
                    application.status = ShopApplicationStatus.REJECTED.value
                    application.rejection_reason = rejection_reason.strip() if rejection_reason else None
                    application.created_shop_id = None
                application.reviewer_user_id = reviewer_user_id
                application.reviewed_at = func.current_timestamp()
                session.flush()
                session.refresh(application)
                response = _response(application)
                outcome = application.status
        except IntegrityError as exc:
            if _is_duplicate_key(exc):
                raise ShopApplicationConflict("shop owner or merchant shop name is already in use") from exc
            raise
        audit_writer(
            actor_user_id=reviewer_user_id,
            event_type="shop_application_reviewed",
            target_type="shop_application",
            target_id=application_id,
            outcome=outcome,
            event_data={"decision": decision.value},
        )
        return response

    @staticmethod
    def list_shops(
        session: Session, *, role: Role, merchant_id: int | None, shop_id: int | None,
        limit: int, offset: int,
    ) -> ShopPage:
        if role is Role.MERCHANT_ADMIN and merchant_id is not None:
            scope = Shop.merchant_id == merchant_id
        elif role in (Role.SHOP_OWNER, Role.SHOP_EMPLOYEE) and shop_id is not None:
            scope = Shop.id == shop_id
        else:
            raise ShopApplicationForbidden("merchant administrator or shop owner required")
        total = session.scalar(select(func.count(Shop.id)).where(scope)) or 0
        shops = session.scalars(
            select(Shop).where(scope).order_by(Shop.id).limit(limit).offset(offset)
        ).all()
        return ShopPage(
            items=[ShopResponse.model_validate(shop, from_attributes=True) for shop in shops],
            limit=limit, offset=offset, total=total,
        )


def _is_duplicate_key(exc: IntegrityError) -> bool:
    original = exc.orig
    args = getattr(original, "args", ())
    return bool(args and args[0] == 1062)

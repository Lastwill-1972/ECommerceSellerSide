from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.permissions import AccessDenied, AuthorizationService
from app.modules.catalog.models import ProductOffshelfApplication, Spu
from app.modules.catalog.offshelf_schemas import (
    OffshelfApplicationPage, OffshelfApplicationResponse, OffshelfDecision, OffshelfSpuSummary,
    OffshelfStatus,
)
from app.modules.users.models import User
from app.services.audit import AuditService
from app.services.identity import Identity, IdentityConflict, IdentityEligibilityError, IdentityService, Role


class OffshelfForbidden(Exception):
    pass


class OffshelfNotFound(Exception):
    pass


class OffshelfConflict(Exception):
    pass


def _fresh(session: Session) -> None:
    if session.in_transaction():
        raise RuntimeError("offshelf write requires a fresh transaction boundary")


def _lock_users(session: Session, user_ids: set[int]) -> dict[int, User]:
    users = session.scalars(
        select(User).where(User.id.in_(sorted(user_ids))).order_by(User.id).with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    result = {row.id: row for row in users}
    if result.keys() != user_ids:
        raise OffshelfForbidden("required active user does not exist")
    return result


def _identity(session: Session, user: User) -> Identity:
    if not user.is_active:
        raise OffshelfForbidden("active user required")
    try:
        return IdentityService.get_identity(session, user)
    except (IdentityConflict, IdentityEligibilityError) as exc:
        raise OffshelfForbidden("user identity is not eligible") from exc


def _response(application: ProductOffshelfApplication, spu: Spu) -> OffshelfApplicationResponse:
    return OffshelfApplicationResponse(
        id=application.id,
        spu_id=application.spu_id,
        applicant_user_id=application.applicant_user_id,
        status=application.status,
        reviewer_user_id=application.reviewer_user_id,
        rejection_reason=application.rejection_reason,
        created_at=application.created_at,
        reviewed_at=application.reviewed_at,
        spu=OffshelfSpuSummary(id=spu.id, name=spu.name, status=spu.status, merchant_id=spu.merchant_id),
    )


def _is_duplicate(exc: IntegrityError) -> bool:
    args = getattr(exc.orig, "args", ())
    return bool(args and args[0] == 1062)


class OffshelfApplicationService:
    @staticmethod
    def list(
        session: Session, *, identity: Identity, limit: int, offset: int, status_filter: OffshelfStatus | None,
    ) -> OffshelfApplicationPage:
        predicates = []
        if identity.role is Role.MERCHANT_ADMIN:
            merchant_id = identity.merchant_id
            if merchant_id is None:
                raise OffshelfForbidden("merchant administrator identity required")
            predicates.append(Spu.merchant_id == merchant_id)
            if status_filter is not None:
                predicates.append(ProductOffshelfApplication.status == status_filter.value)
        elif identity.role is Role.PLATFORM_ADMIN:
            predicates.append(ProductOffshelfApplication.status == OffshelfStatus.PENDING.value)
        else:
            raise OffshelfForbidden("merchant administrator or platform administrator required")
        base = select(ProductOffshelfApplication, Spu).join(Spu, Spu.id == ProductOffshelfApplication.spu_id)
        total = session.scalar(select(func.count()).select_from(base.where(*predicates).subquery())) or 0
        rows = session.execute(
            base.where(*predicates)
            .order_by(ProductOffshelfApplication.id)
            .limit(limit).offset(offset)
        ).all()
        items = [_response(application, spu) for application, spu in rows]
        return OffshelfApplicationPage(items=items, limit=limit, offset=offset, total=total)

    @staticmethod
    def submit(
        session: Session, *, actor_user_id: int, spu_id: int, audit_writer=AuditService.record_after_commit,
    ) -> OffshelfApplicationResponse:
        _fresh(session)
        try:
            with session.begin():
                actor = _lock_users(session, {actor_user_id})[actor_user_id]
                identity = _identity(session, actor)
                try:
                    merchant_id = AuthorizationService.catalog_merchant(identity, write=True)
                except AccessDenied as exc:
                    raise OffshelfForbidden("merchant administrator required") from exc
                spu = session.scalar(
                    select(Spu).where(Spu.id == spu_id).with_for_update()
                    .execution_options(populate_existing=True)
                )
                if spu is None or spu.merchant_id != merchant_id:
                    raise OffshelfNotFound("SPU not found")
                if spu.status != "ON_SALE":
                    raise OffshelfConflict("SPU is not on sale")
                pending = session.scalar(
                    select(ProductOffshelfApplication.id).where(
                        ProductOffshelfApplication.spu_id == spu_id,
                        ProductOffshelfApplication.status == OffshelfStatus.PENDING.value,
                    ).limit(1)
                )
                if pending is not None:
                    raise OffshelfConflict("a pending application already exists for this SPU")
                application = ProductOffshelfApplication(
                    spu_id=spu_id, applicant_user_id=actor_user_id, status=OffshelfStatus.PENDING.value,
                )
                session.add(application)
                session.flush()
                session.refresh(application)
                response = _response(application, spu)
                application_id = application.id
        except IntegrityError as exc:
            if _is_duplicate(exc):
                raise OffshelfConflict("a pending application already exists for this SPU") from exc
            raise
        audit_writer(
            actor_user_id=actor_user_id, event_type="catalog_offshelf_application_submitted",
            target_type="product_offshelf_application", target_id=application_id, outcome="PENDING",
            event_data={"spu_id": spu_id, "applicant_user_id": actor_user_id},
        )
        return response

    @staticmethod
    def review(
        session: Session, *, reviewer_user_id: int, application_id: int, decision: OffshelfDecision,
        rejection_reason: str | None, audit_writer=AuditService.record_after_commit,
    ) -> OffshelfApplicationResponse:
        _fresh(session)
        # Discover immutable user/SPU keys in a disposable read transaction. The
        # subsequent write transaction starts with user locks so IdentityService's
        # relationship reads get a current snapshot after any lock wait.
        target = session.execute(
            select(ProductOffshelfApplication.spu_id, ProductOffshelfApplication.applicant_user_id)
            .where(ProductOffshelfApplication.id == application_id)
        ).one_or_none()
        if target is None:
            session.rollback()
            raise OffshelfNotFound("application not found")
        spu_id, applicant_user_id = target
        session.rollback()
        with session.begin():
            users = _lock_users(session, {reviewer_user_id, applicant_user_id})
            reviewer = users[reviewer_user_id]
            reviewer_identity = _identity(session, reviewer)
            if reviewer_identity.role is not Role.PLATFORM_ADMIN:
                raise OffshelfForbidden("platform administrator required")
            if reviewer_user_id == applicant_user_id:
                raise OffshelfForbidden("applicants cannot review their own applications")
            spu = session.scalar(
                select(Spu).where(Spu.id == spu_id).with_for_update()
                .execution_options(populate_existing=True)
            )
            if spu is None:
                raise OffshelfNotFound("SPU not found")
            application = session.scalar(
                select(ProductOffshelfApplication).where(ProductOffshelfApplication.id == application_id)
                .with_for_update().execution_options(populate_existing=True)
            )
            if application is None:
                raise OffshelfNotFound("application not found")
            if application.spu_id != spu_id or application.applicant_user_id != applicant_user_id:
                raise OffshelfConflict("application changed during review")
            if application.status != OffshelfStatus.PENDING.value:
                raise OffshelfConflict("application has already been reviewed")
            if decision is OffshelfDecision.APPROVE:
                if spu.status != "ON_SALE":
                    raise OffshelfConflict("SPU is no longer on sale")
                applicant_identity = _identity(session, users[applicant_user_id])
                if applicant_identity.role is not Role.MERCHANT_ADMIN or applicant_identity.merchant_id != spu.merchant_id:
                    raise OffshelfConflict("applicant is no longer the SPU merchant administrator")
                spu.status = "OFF_SALE"
                application.status = OffshelfStatus.APPROVED.value
                application.rejection_reason = None
            else:
                application.status = OffshelfStatus.REJECTED.value
                application.rejection_reason = rejection_reason
            application.reviewer_user_id = reviewer_user_id
            application.reviewed_at = func.current_timestamp()
            session.flush()
            session.refresh(spu)
            session.refresh(application)
            response = _response(application, spu)
            outcome = application.status
        audit_writer(
            actor_user_id=reviewer_user_id, event_type="catalog_offshelf_application_reviewed",
            target_type="product_offshelf_application", target_id=application_id, outcome=outcome,
            event_data={"spu_id": spu_id, "reviewer_user_id": reviewer_user_id, "decision": decision.value},
        )
        return response

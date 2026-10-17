from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.employees.models import EmployeeApplication, ShopEmployee, ShopEmployeeRole
from app.modules.employees.schemas import (
    EmployeeApplicationDecision,
    EmployeeApplicationPage,
    EmployeeApplicationResponse,
    EmployeeApplicationStatus,
    ShopEmployeePage,
    ShopEmployeeResponse,
    ShopEmployeeRolePage,
    ShopEmployeeRoleResponse,
)
from app.modules.shops.models import Shop
from app.modules.users.models import User
from app.services.audit import AuditService
from app.services.identity import IdentityConflict, IdentityEligibilityError, IdentityService, Role


class EmployeeConflict(Exception):
    pass


class EmployeeNotFound(Exception):
    pass


class EmployeeForbidden(Exception):
    pass


class EmployeeIneligible(Exception):
    pass


def _fresh(session: Session) -> None:
    if session.in_transaction():
        raise RuntimeError("write service requires a fresh transaction boundary")


def _lock_users(
    session: Session,
    ids: set[int],
    *,
    missing_user_ids_as_not_found: set[int] | None = None,
) -> dict[int, User]:
    users = session.scalars(
        select(User)
        .where(User.id.in_(sorted(ids)))
        .order_by(User.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    result = {user.id: user for user in users}
    missing_ids = ids - result.keys()
    if missing_ids and missing_user_ids_as_not_found and missing_ids <= missing_user_ids_as_not_found:
        raise EmployeeNotFound("shop employee not found")
    if missing_ids:
        raise EmployeeIneligible("required active user does not exist")
    return result


def _app_response(application: EmployeeApplication) -> EmployeeApplicationResponse:
    return EmployeeApplicationResponse.model_validate(application, from_attributes=True)


def _employee_response(employee: ShopEmployee, role_name: str) -> ShopEmployeeResponse:
    return ShopEmployeeResponse(
        id=employee.id,
        shop_id=employee.shop_id,
        user_id=employee.user_id,
        role_id=employee.role_id,
        role_name=role_name,
        joined_at=employee.joined_at,
    )


def _role_response(role: ShopEmployeeRole) -> ShopEmployeeRoleResponse:
    return ShopEmployeeRoleResponse.model_validate(role, from_attributes=True)


def _is_duplicate(exc: IntegrityError) -> bool:
    args = getattr(exc.orig, "args", ())
    return bool(args and args[0] == 1062)


def _require_owner(session: Session, user: User, shop_id: int) -> None:
    if not user.is_active:
        raise EmployeeIneligible("active shop owner required")
    try:
        identity = IdentityService.get_identity(session, user)
    except IdentityEligibilityError as exc:
        raise EmployeeIneligible("active shop owner required") from exc
    except IdentityConflict as exc:
        raise EmployeeIneligible("conflicting identity relationships") from exc
    if identity.role is not Role.SHOP_OWNER or identity.shop_id != shop_id:
        raise EmployeeForbidden("current owner of this shop required")


class EmployeeService:
    DEFAULT_ROLE_NAME = "普通员工"

    @staticmethod
    def end_read_only_transaction(session: Session) -> None:
        if session.new or session.dirty or session.deleted:
            raise RuntimeError("cannot discard a Session containing pending changes")
        if session.in_transaction():
            session.rollback()

    @staticmethod
    def prepare_submission(session: Session, shop_id: int) -> int:
        owner_id = session.scalar(select(Shop.owner_user_id).where(Shop.id == shop_id))
        if owner_id is None:
            raise EmployeeNotFound("shop not found")
        return int(owner_id)

    @staticmethod
    def prepare_review(session: Session, application_id: int) -> tuple[int, int, int]:
        row = session.execute(
            select(EmployeeApplication.applicant_user_id, EmployeeApplication.shop_id, Shop.owner_user_id)
            .join(Shop, Shop.id == EmployeeApplication.shop_id)
            .where(EmployeeApplication.id == application_id)
        ).one_or_none()
        if row is None:
            raise EmployeeNotFound("application not found")
        return int(row.applicant_user_id), int(row.shop_id), int(row.owner_user_id)

    @staticmethod
    def submit(
        session: Session,
        *,
        applicant_user_id: int,
        shop_id: int,
        expected_owner_user_id: int,
        audit_writer=AuditService.record_after_commit,
    ) -> EmployeeApplicationResponse:
        _fresh(session)
        try:
            with session.begin():
                users = _lock_users(session, {applicant_user_id, expected_owner_user_id})
                applicant, owner = users[applicant_user_id], users[expected_owner_user_id]
                if not applicant.is_active:
                    raise EmployeeIneligible("active applicant required")
                shop = session.get(Shop, shop_id)
                if shop is None:
                    raise EmployeeNotFound("shop not found")
                if shop.owner_user_id != expected_owner_user_id:
                    raise EmployeeConflict("shop owner changed; retry the request")
                try:
                    applicant_identity = IdentityService.get_identity(session, applicant)
                except IdentityConflict as exc:
                    raise EmployeeIneligible("conflicting identity relationships") from exc
                if applicant_identity.role is not Role.NONE:
                    raise EmployeeIneligible("applicant must have no existing platform or tenant identity")
                pending = session.scalar(
                    select(EmployeeApplication.id)
                    .where(
                        EmployeeApplication.applicant_user_id == applicant_user_id,
                        EmployeeApplication.status == EmployeeApplicationStatus.PENDING.value,
                    )
                    .limit(1)
                )
                if pending is not None:
                    raise EmployeeConflict("an employee application is already pending")
                application = EmployeeApplication(shop_id=shop_id, applicant_user_id=applicant_user_id)
                session.add(application)
                session.flush()
                session.refresh(application)
                response = _app_response(application)
                application_id = application.id
        except IntegrityError as exc:
            if _is_duplicate(exc):
                raise EmployeeConflict("a unique employee constraint was violated") from exc
            raise
        audit_writer(
            actor_user_id=applicant_user_id,
            event_type="employee_application_submitted",
            target_type="employee_application",
            target_id=application_id,
            outcome="PENDING",
            event_data={},
        )
        return response

    @staticmethod
    def list_applications(
        session: Session,
        *,
        actor_user_id: int,
        role: Role,
        shop_id: int | None,
        status_filter: EmployeeApplicationStatus | None,
        limit: int,
        offset: int,
    ) -> EmployeeApplicationPage:
        if role is Role.SHOP_OWNER and shop_id is not None:
            scope = EmployeeApplication.shop_id == shop_id
        else:
            scope = EmployeeApplication.applicant_user_id == actor_user_id
        if status_filter is not None:
            scope = scope & (EmployeeApplication.status == status_filter.value)
        total = session.scalar(select(func.count(EmployeeApplication.id)).where(scope)) or 0
        rows = session.scalars(
            select(EmployeeApplication)
            .where(scope)
            .order_by(EmployeeApplication.created_at.desc(), EmployeeApplication.id.desc())
            .limit(limit)
            .offset(offset)
        ).all()
        return EmployeeApplicationPage(
            items=[_app_response(row) for row in rows], limit=limit, offset=offset, total=total
        )

    @staticmethod
    def review(
        session: Session,
        *,
        reviewer_user_id: int,
        application_id: int,
        expected_applicant_user_id: int,
        expected_shop_id: int,
        expected_owner_user_id: int,
        decision: EmployeeApplicationDecision,
        rejection_reason: str | None,
        audit_writer=AuditService.record_after_commit,
    ) -> EmployeeApplicationResponse:
        _fresh(session)
        try:
            with session.begin():
                users = _lock_users(session, {reviewer_user_id, expected_applicant_user_id})
                reviewer, applicant = users[reviewer_user_id], users[expected_applicant_user_id]
                if not reviewer.is_active:
                    raise EmployeeIneligible("reviewer must be active")
                application = session.scalar(
                    select(EmployeeApplication)
                    .where(EmployeeApplication.id == application_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if application is None:
                    raise EmployeeNotFound("application not found")
                if (
                    application.applicant_user_id != expected_applicant_user_id
                    or application.shop_id != expected_shop_id
                ):
                    raise EmployeeConflict("application changed during review")
                if application.status != EmployeeApplicationStatus.PENDING.value:
                    raise EmployeeConflict("application has already been reviewed")
                if reviewer_user_id == expected_applicant_user_id:
                    raise EmployeeForbidden("applicants cannot review their own applications")
                shop = session.get(Shop, expected_shop_id)
                if shop is None or shop.owner_user_id != expected_owner_user_id:
                    raise EmployeeForbidden("shop ownership changed during review")
                _require_owner(session, reviewer, expected_shop_id)
                if reviewer_user_id != expected_owner_user_id:
                    raise EmployeeForbidden("only the current shop owner may review")

                if decision is EmployeeApplicationDecision.APPROVE:
                    if not applicant.is_active:
                        raise EmployeeIneligible("applicant must be active")
                    try:
                        identity = IdentityService.get_identity(session, applicant)
                    except (IdentityConflict, IdentityEligibilityError) as exc:
                        raise EmployeeIneligible("conflicting identity relationships") from exc
                    if identity.role is not Role.NONE:
                        raise EmployeeIneligible("applicant must have no existing platform or tenant identity")
                    default_role = session.scalar(
                        select(ShopEmployeeRole)
                        .where(
                            ShopEmployeeRole.shop_id == expected_shop_id,
                            ShopEmployeeRole.name == EmployeeService.DEFAULT_ROLE_NAME,
                        )
                        .with_for_update()
                    )
                    if default_role is None:
                        raise EmployeeConflict("shop default employee role is missing")
                    session.add(
                        ShopEmployee(
                            shop_id=expected_shop_id,
                            user_id=expected_applicant_user_id,
                            role_id=default_role.id,
                        )
                    )
                    application.status = EmployeeApplicationStatus.APPROVED.value
                    application.rejection_reason = None
                else:
                    application.status = EmployeeApplicationStatus.REJECTED.value
                    application.rejection_reason = rejection_reason.strip() if rejection_reason else None
                application.reviewer_user_id = reviewer_user_id
                application.reviewed_at = func.current_timestamp()
                session.flush()
                session.refresh(application)
                response = _app_response(application)
                outcome = application.status
        except IntegrityError as exc:
            if _is_duplicate(exc):
                raise EmployeeConflict("user already belongs to a shop") from exc
            raise
        audit_writer(
            actor_user_id=reviewer_user_id,
            event_type="employee_application_reviewed",
            target_type="employee_application",
            target_id=application_id,
            outcome=outcome,
            event_data={"decision": decision.value},
        )
        return response

    @staticmethod
    def list_employees(
        session: Session, *, actor_user_id: int, shop_id: int, limit: int, offset: int
    ) -> ShopEmployeePage:
        owner = session.get(User, actor_user_id)
        if owner is None:
            raise EmployeeForbidden("current shop owner required")
        _require_owner(session, owner, shop_id)
        total = session.scalar(
            select(func.count(ShopEmployee.id)).where(ShopEmployee.shop_id == shop_id)
        ) or 0
        rows = session.execute(
            select(ShopEmployee, ShopEmployeeRole.name)
            .join(
                ShopEmployeeRole,
                (ShopEmployeeRole.shop_id == ShopEmployee.shop_id)
                & (ShopEmployeeRole.id == ShopEmployee.role_id),
            )
            .where(ShopEmployee.shop_id == shop_id)
            .order_by(ShopEmployee.id)
            .limit(limit)
            .offset(offset)
        ).all()
        return ShopEmployeePage(
            items=[_employee_response(employee, name) for employee, name in rows],
            limit=limit,
            offset=offset,
            total=total,
        )

    @staticmethod
    def remove_employee(
        session: Session,
        *,
        actor_user_id: int,
        shop_id: int,
        employee_user_id: int,
        audit_writer=AuditService.record_after_commit,
    ) -> None:
        _fresh(session)
        with session.begin():
            users = _lock_users(
                session,
                {actor_user_id, employee_user_id},
                missing_user_ids_as_not_found={employee_user_id},
            )
            _require_owner(session, users[actor_user_id], shop_id)
            employee = session.scalar(
                select(ShopEmployee)
                .where(ShopEmployee.shop_id == shop_id, ShopEmployee.user_id == employee_user_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if employee is None:
                raise EmployeeNotFound("shop employee not found")
            employee_id = employee.id
            session.delete(employee)
        audit_writer(
            actor_user_id=actor_user_id,
            event_type="shop_employee_removed",
            target_type="shop_employee",
            target_id=employee_id,
            outcome="REMOVED",
            event_data={"shop_id": shop_id, "user_id": employee_user_id},
        )

    @staticmethod
    def list_roles(
        session: Session, *, actor_user_id: int, shop_id: int, limit: int, offset: int
    ) -> ShopEmployeeRolePage:
        owner = session.get(User, actor_user_id)
        if owner is None:
            raise EmployeeForbidden("current shop owner required")
        _require_owner(session, owner, shop_id)
        scope = ShopEmployeeRole.shop_id == shop_id
        total = session.scalar(select(func.count(ShopEmployeeRole.id)).where(scope)) or 0
        roles = session.scalars(
            select(ShopEmployeeRole).where(scope).order_by(ShopEmployeeRole.id).limit(limit).offset(offset)
        ).all()
        return ShopEmployeeRolePage(
            items=[_role_response(role) for role in roles], limit=limit, offset=offset, total=total
        )

    @staticmethod
    def create_role(
        session: Session,
        *,
        actor_user_id: int,
        shop_id: int,
        name: str,
        audit_writer=AuditService.record_after_commit,
    ) -> ShopEmployeeRoleResponse:
        _fresh(session)
        try:
            with session.begin():
                users = _lock_users(session, {actor_user_id})
                _require_owner(session, users[actor_user_id], shop_id)
                if session.get(Shop, shop_id) is None:
                    raise EmployeeNotFound("shop not found")
                role = ShopEmployeeRole(shop_id=shop_id, name=name)
                session.add(role)
                session.flush()
                session.refresh(role)
                response = _role_response(role)
                role_id = role.id
        except IntegrityError as exc:
            if _is_duplicate(exc):
                raise EmployeeConflict("role name already exists for this shop") from exc
            raise
        audit_writer(
            actor_user_id=actor_user_id,
            event_type="shop_employee_role_created",
            target_type="shop_employee_role",
            target_id=role_id,
            outcome="CREATED",
            event_data={"shop_id": shop_id},
        )
        return response

    @staticmethod
    def update_employee_role(
        session: Session,
        *,
        actor_user_id: int,
        shop_id: int,
        employee_user_id: int,
        role_id: int,
        audit_writer=AuditService.record_after_commit,
    ) -> ShopEmployeeResponse:
        _fresh(session)
        with session.begin():
            users = _lock_users(
                session,
                {actor_user_id, employee_user_id},
                missing_user_ids_as_not_found={employee_user_id},
            )
            _require_owner(session, users[actor_user_id], shop_id)
            employee = session.scalar(
                select(ShopEmployee)
                .where(ShopEmployee.shop_id == shop_id, ShopEmployee.user_id == employee_user_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if employee is None:
                raise EmployeeNotFound("shop employee not found")
            role = session.scalar(
                select(ShopEmployeeRole)
                .where(ShopEmployeeRole.shop_id == shop_id, ShopEmployeeRole.id == role_id)
                .with_for_update()
            )
            if role is None:
                raise EmployeeNotFound("employee role not found for this shop")
            employee.role_id = role.id
            session.flush()
            response = _employee_response(employee, role.name)
            target_id = employee.id
        audit_writer(
            actor_user_id=actor_user_id,
            event_type="shop_employee_role_changed",
            target_type="shop_employee",
            target_id=target_id,
            outcome="UPDATED",
            event_data={"shop_id": shop_id, "role_id": role_id},
        )
        return response

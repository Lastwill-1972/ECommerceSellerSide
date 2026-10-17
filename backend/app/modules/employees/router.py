from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status

from app.api.deps import CurrentIdentity, CurrentUser, DbSession
from app.modules.employees.schemas import (
    EmployeeApplicationCreate,
    EmployeeApplicationPage,
    EmployeeApplicationReview,
    EmployeeApplicationResponse,
    EmployeeApplicationStatus,
    ShopEmployeePage,
    ShopEmployeeRoleCreate,
    ShopEmployeeRolePage,
    ShopEmployeeRoleResponse,
    ShopEmployeeRoleUpdate,
    ShopEmployeeResponse,
)
from app.services.employees import (
    EmployeeConflict,
    EmployeeForbidden,
    EmployeeIneligible,
    EmployeeNotFound,
    EmployeeService,
)
from app.services.identity import Role

router = APIRouter(tags=["employees"])
application_router = APIRouter(prefix="/employees/applications")
shop_router = APIRouter(prefix="/shops/{shop_id}/employees")
role_router = APIRouter(prefix="/shops/{shop_id}/employee-roles")


@application_router.post("", response_model=EmployeeApplicationResponse, status_code=status.HTTP_201_CREATED)
def submit_application(
    request: EmployeeApplicationCreate,
    user: CurrentUser,
    identity: CurrentIdentity,
    session: DbSession,
) -> EmployeeApplicationResponse:
    if identity.role is not Role.NONE:
        raise HTTPException(status_code=409, detail="applicant must have no existing platform or tenant identity")
    applicant_user_id = int(user.id)
    try:
        expected_owner_user_id = EmployeeService.prepare_submission(session, request.shop_id)
    except EmployeeNotFound as exc:
        raise HTTPException(status_code=404, detail="shop not found") from exc
    EmployeeService.end_read_only_transaction(session)
    try:
        return EmployeeService.submit(
            session,
            applicant_user_id=applicant_user_id,
            shop_id=request.shop_id,
            expected_owner_user_id=expected_owner_user_id,
        )
    except EmployeeConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except EmployeeIneligible as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except EmployeeForbidden as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@application_router.get("", response_model=EmployeeApplicationPage)
def list_applications(
    user: CurrentUser,
    identity: CurrentIdentity,
    session: DbSession,
    status_filter: Annotated[EmployeeApplicationStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> EmployeeApplicationPage:
    return EmployeeService.list_applications(
        session,
        actor_user_id=int(user.id),
        role=identity.role,
        shop_id=identity.shop_id,
        status_filter=status_filter,
        limit=limit,
        offset=offset,
    )


@application_router.post("/{application_id}/review", response_model=EmployeeApplicationResponse)
def review_application(
    application_id: int,
    request: EmployeeApplicationReview,
    user: CurrentUser,
    identity: CurrentIdentity,
    session: DbSession,
) -> EmployeeApplicationResponse:
    if identity.role is not Role.SHOP_OWNER or identity.shop_id is None:
        raise HTTPException(status_code=403, detail="current shop owner required")
    reviewer_user_id = int(user.id)
    try:
        applicant_user_id, expected_shop_id, expected_owner_user_id = EmployeeService.prepare_review(
            session, application_id
        )
    except EmployeeNotFound as exc:
        raise HTTPException(status_code=404, detail="application not found") from exc
    EmployeeService.end_read_only_transaction(session)
    if expected_shop_id != identity.shop_id or expected_owner_user_id != reviewer_user_id:
        raise HTTPException(status_code=403, detail="application belongs to another shop")
    try:
        return EmployeeService.review(
            session,
            reviewer_user_id=reviewer_user_id,
            application_id=application_id,
            expected_applicant_user_id=applicant_user_id,
            expected_shop_id=expected_shop_id,
            expected_owner_user_id=expected_owner_user_id,
            decision=request.decision,
            rejection_reason=request.rejection_reason,
        )
    except EmployeeNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except EmployeeConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except EmployeeForbidden as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except EmployeeIneligible as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@shop_router.get("", response_model=ShopEmployeePage)
def list_employees(
    shop_id: int,
    user: CurrentUser,
    identity: CurrentIdentity,
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> ShopEmployeePage:
    if identity.role is not Role.SHOP_OWNER:
        raise HTTPException(status_code=403, detail="current shop owner required")
    try:
        return EmployeeService.list_employees(
            session, actor_user_id=int(user.id), shop_id=shop_id, limit=limit, offset=offset
        )
    except EmployeeForbidden as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except EmployeeIneligible as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@shop_router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_employee(
    shop_id: int,
    user_id: int,
    user: CurrentUser,
    identity: CurrentIdentity,
    session: DbSession,
) -> Response:
    if identity.role is not Role.SHOP_OWNER:
        raise HTTPException(status_code=403, detail="current shop owner required")
    actor_user_id = int(user.id)
    EmployeeService.end_read_only_transaction(session)
    try:
        EmployeeService.remove_employee(
            session, actor_user_id=actor_user_id, shop_id=shop_id, employee_user_id=user_id
        )
    except EmployeeNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except EmployeeForbidden as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except EmployeeIneligible as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@role_router.get("", response_model=ShopEmployeeRolePage)
def list_roles(
    shop_id: int,
    user: CurrentUser,
    identity: CurrentIdentity,
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> ShopEmployeeRolePage:
    if identity.role is not Role.SHOP_OWNER:
        raise HTTPException(status_code=403, detail="current shop owner required")
    try:
        return EmployeeService.list_roles(
            session, actor_user_id=int(user.id), shop_id=shop_id, limit=limit, offset=offset
        )
    except EmployeeForbidden as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except EmployeeIneligible as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@role_router.post("", response_model=ShopEmployeeRoleResponse, status_code=status.HTTP_201_CREATED)
def create_role(
    shop_id: int,
    request: ShopEmployeeRoleCreate,
    user: CurrentUser,
    identity: CurrentIdentity,
    session: DbSession,
) -> ShopEmployeeRoleResponse:
    if identity.role is not Role.SHOP_OWNER:
        raise HTTPException(status_code=403, detail="current shop owner required")
    actor_user_id = int(user.id)
    EmployeeService.end_read_only_transaction(session)
    try:
        return EmployeeService.create_role(
            session, actor_user_id=actor_user_id, shop_id=shop_id, name=request.name
        )
    except EmployeeNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except EmployeeConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except EmployeeForbidden as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except EmployeeIneligible as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@shop_router.patch("/{user_id}/role", response_model=ShopEmployeeResponse)
def update_employee_role(
    shop_id: int,
    user_id: int,
    request: ShopEmployeeRoleUpdate,
    user: CurrentUser,
    identity: CurrentIdentity,
    session: DbSession,
) -> ShopEmployeeResponse:
    if identity.role is not Role.SHOP_OWNER:
        raise HTTPException(status_code=403, detail="current shop owner required")
    actor_user_id = int(user.id)
    EmployeeService.end_read_only_transaction(session)
    try:
        return EmployeeService.update_employee_role(
            session,
            actor_user_id=actor_user_id,
            shop_id=shop_id,
            employee_user_id=user_id,
            role_id=request.role_id,
        )
    except EmployeeNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except EmployeeForbidden as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except EmployeeIneligible as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


router.include_router(application_router)
router.include_router(shop_router)
router.include_router(role_router)

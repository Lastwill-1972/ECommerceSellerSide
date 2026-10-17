from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import CurrentIdentity, CurrentUser, DbSession
from app.modules.merchants.schemas import (
    ApplicationDecision,
    ApplicationStatus,
    MerchantApplicationCreate,
    MerchantApplicationPage,
    MerchantApplicationResponse,
    MerchantApplicationReview,
    MerchantDirectoryPage,
    MerchantProfileUpdate,
    MerchantProfileResponse,
)
from app.services.merchant_directory import MerchantDirectoryService
from app.services.merchant_profile import MerchantProfileConflict, MerchantProfileForbidden, MerchantProfileService
from app.services.identity import Role
from app.services.merchant_applications import (
    ApplicationConflict,
    ApplicationForbidden,
    ApplicationIneligible,
    ApplicationNotFound,
    MerchantApplicationService,
)

router = APIRouter(prefix="/merchants", tags=["merchants"])


@router.patch("/me", response_model=MerchantProfileResponse)
def update_my_merchant(
    request: MerchantProfileUpdate,
    user: CurrentUser,
    identity: CurrentIdentity,
    session: DbSession,
) -> MerchantProfileResponse:
    if identity.role is not Role.MERCHANT_ADMIN or identity.merchant_id is None:
        raise HTTPException(status_code=403, detail="merchant administrator required")
    actor_user_id = int(user.id)
    merchant_id = int(identity.merchant_id)
    MerchantApplicationService.end_read_only_transaction(session)
    try:
        return MerchantProfileService.update_name(
            session, actor_user_id=actor_user_id, merchant_id=merchant_id, name=request.name
        )
    except MerchantProfileForbidden as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except MerchantProfileConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("", response_model=MerchantDirectoryPage)
def list_merchants(
    _user: CurrentUser,
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> MerchantDirectoryPage:
    return MerchantDirectoryService.list_directory(session, limit=limit, offset=offset)


@router.post(
    "/applications",
    response_model=MerchantApplicationResponse,
    status_code=status.HTTP_201_CREATED,
)
def submit_application(
    request: MerchantApplicationCreate,
    user: CurrentUser,
    identity: CurrentIdentity,
    session: DbSession,
) -> MerchantApplicationResponse:
    if identity.role is not Role.NONE:
        raise HTTPException(status_code=409, detail="applicant must have no existing platform or tenant identity")
    applicant_user_id = int(user.id)
    MerchantApplicationService.end_read_only_transaction(session)
    try:
        return MerchantApplicationService.submit(
            session,
            applicant_user_id=applicant_user_id,
            merchant_name=request.merchant_name,
        )
    except ApplicationConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ApplicationIneligible as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/applications", response_model=MerchantApplicationPage)
def list_applications(
    user: CurrentUser,
    identity: CurrentIdentity,
    session: DbSession,
    status_filter: Annotated[ApplicationStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> MerchantApplicationPage:
    try:
        return MerchantApplicationService.list_applications(
            session,
            actor_user_id=int(user.id),
            platform_admin=identity.role is Role.PLATFORM_ADMIN,
            status_filter=status_filter,
            limit=limit,
            offset=offset,
        )
    except ApplicationForbidden as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/applications/{application_id}/review", response_model=MerchantApplicationResponse)
def review_application(
    application_id: int,
    request: MerchantApplicationReview,
    user: CurrentUser,
    identity: CurrentIdentity,
    session: DbSession,
) -> MerchantApplicationResponse:
    if identity.role is not Role.PLATFORM_ADMIN:
        raise HTTPException(status_code=403, detail="active platform administrator required")
    reviewer_user_id = int(user.id)
    try:
        expected_applicant_user_id = MerchantApplicationService.prepare_review_applicant(session, application_id)
    except ApplicationNotFound as exc:
        raise HTTPException(status_code=404, detail="application not found") from exc
    MerchantApplicationService.end_read_only_transaction(session)
    try:
        return MerchantApplicationService.review(
            session,
            reviewer_user_id=reviewer_user_id,
            application_id=application_id,
            expected_applicant_user_id=expected_applicant_user_id,
            decision=request.decision,
            rejection_reason=request.rejection_reason,
        )
    except ApplicationNotFound as exc:
        raise HTTPException(status_code=404, detail="application not found") from exc
    except ApplicationConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ApplicationForbidden as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ApplicationIneligible as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

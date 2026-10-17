from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import CurrentIdentity, CurrentUser, DbSession
from app.modules.shops.schemas import (
    ShopApplicationCreate,
    ShopApplicationDecision,
    ShopApplicationPage,
    ShopApplicationResponse,
    ShopApplicationReview,
    ShopApplicationStatus,
    ShopPage,
    OwnerTransferRequest,
    OwnerTransferResponse,
)
from app.services.identity import Role
from app.services.owner_transfers import (
    OwnerTransferConflict, OwnerTransferForbidden, OwnerTransferNotFound, OwnerTransferService,
)
from app.services.shop_applications import (
    ShopApplicationConflict,
    ShopApplicationForbidden,
    ShopApplicationIneligible,
    ShopApplicationNotFound,
    ShopApplicationService,
)

router = APIRouter(prefix="/shops", tags=["shops"])


@router.put("/{shop_id}/owner", response_model=OwnerTransferResponse)
def transfer_shop_owner(
    shop_id: int,
    request: OwnerTransferRequest,
    user: CurrentUser,
    identity: CurrentIdentity,
    session: DbSession,
) -> OwnerTransferResponse:
    actor_user_id = int(user.id)
    if request.action == "PROPOSE":
        if identity.role is not Role.SHOP_OWNER or identity.shop_id != shop_id:
            raise HTTPException(status_code=403, detail="current shop owner required")
        proposal_id = None
    elif request.action == "CONFIRM":
        if identity.role is not Role.MERCHANT_ADMIN or identity.merchant_id is None:
            raise HTTPException(status_code=403, detail="merchant administrator required")
        proposal_id = request.proposal_id
    else:
        if identity.role is not Role.MERCHANT_ADMIN or identity.merchant_id is None:
            raise HTTPException(status_code=403, detail="merchant administrator required")
        proposal_id = None
    try:
        expected_owner, expected_merchant, expected_admin, proposal_ids = OwnerTransferService.prepare(
            session, shop_id, proposal_id=proposal_id
        )
    except OwnerTransferNotFound as exc:
        raise HTTPException(status_code=404, detail="shop not found") from exc
    OwnerTransferService.end_read_only_transaction(session)
    if request.action == "PROPOSE":
        target_user_id, reason = request.target_user_id, None
    elif request.action == "CONFIRM":
        target_user_id, reason = None, None
        if identity.merchant_id != expected_merchant:
            raise HTTPException(status_code=403, detail="shop belongs to another merchant")
    else:
        target_user_id, reason = request.target_user_id, request.reason
        if identity.merchant_id != expected_merchant:
            raise HTTPException(status_code=403, detail="shop belongs to another merchant")
    try:
        return OwnerTransferService.execute(
            session, actor_user_id=actor_user_id, shop_id=shop_id, expected_owner_id=expected_owner,
            expected_merchant_id=expected_merchant, expected_admin_id=expected_admin,
            expected_proposal_ids=proposal_ids, action=request.action, target_user_id=target_user_id,
            proposal_id=proposal_id, reason=reason,
        )
    except OwnerTransferNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except OwnerTransferForbidden as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except OwnerTransferConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/applications",
    response_model=ShopApplicationResponse,
    status_code=status.HTTP_201_CREATED,
)
def submit_application(
    request: ShopApplicationCreate,
    user: CurrentUser,
    identity: CurrentIdentity,
    session: DbSession,
) -> ShopApplicationResponse:
    if identity.role is not Role.NONE:
        raise HTTPException(status_code=409, detail="applicant must have no existing platform or tenant identity")
    applicant_user_id = int(user.id)
    try:
        expected_admin_user_id = ShopApplicationService.prepare_submission(session, request.merchant_id)
    except ShopApplicationNotFound as exc:
        raise HTTPException(status_code=404, detail="merchant not found") from exc
    ShopApplicationService.end_read_only_transaction(session)
    try:
        return ShopApplicationService.submit(
            session,
            applicant_user_id=applicant_user_id,
            merchant_id=request.merchant_id,
            expected_admin_user_id=expected_admin_user_id,
            shop_name=request.shop_name,
        )
    except ShopApplicationConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ShopApplicationIneligible as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/applications", response_model=ShopApplicationPage)
def list_applications(
    user: CurrentUser,
    identity: CurrentIdentity,
    session: DbSession,
    status_filter: Annotated[ShopApplicationStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> ShopApplicationPage:
    return ShopApplicationService.list_applications(
        session,
        actor_user_id=int(user.id),
        role=identity.role,
        merchant_id=identity.merchant_id,
        status_filter=status_filter,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/applications/{application_id}/review",
    response_model=ShopApplicationResponse,
)
def review_application(
    application_id: int,
    request: ShopApplicationReview,
    user: CurrentUser,
    identity: CurrentIdentity,
    session: DbSession,
) -> ShopApplicationResponse:
    if identity.role is not Role.MERCHANT_ADMIN or identity.merchant_id is None:
        raise HTTPException(status_code=403, detail="merchant administrator required")
    reviewer_user_id = int(user.id)
    try:
        expected_applicant_user_id, expected_merchant_id, expected_admin_user_id = (
            ShopApplicationService.prepare_review(session, application_id)
        )
    except ShopApplicationNotFound as exc:
        raise HTTPException(status_code=404, detail="application not found") from exc
    ShopApplicationService.end_read_only_transaction(session)
    if expected_merchant_id != identity.merchant_id or expected_admin_user_id != reviewer_user_id:
        raise HTTPException(status_code=403, detail="application belongs to another merchant")
    try:
        return ShopApplicationService.review(
            session,
            reviewer_user_id=reviewer_user_id,
            application_id=application_id,
            expected_applicant_user_id=expected_applicant_user_id,
            expected_merchant_id=expected_merchant_id,
            expected_admin_user_id=expected_admin_user_id,
            decision=request.decision,
            rejection_reason=request.rejection_reason,
        )
    except ShopApplicationNotFound as exc:
        raise HTTPException(status_code=404, detail="application not found") from exc
    except ShopApplicationConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ShopApplicationForbidden as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ShopApplicationIneligible as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("", response_model=ShopPage)
def list_shops(
    _user: CurrentUser,
    identity: CurrentIdentity,
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> ShopPage:
    try:
        return ShopApplicationService.list_shops(
            session,
            role=identity.role,
            merchant_id=identity.merchant_id,
            shop_id=identity.shop_id,
            limit=limit,
            offset=offset,
        )
    except ShopApplicationForbidden as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

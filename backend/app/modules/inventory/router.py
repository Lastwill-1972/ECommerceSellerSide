from typing import Annotated

from fastapi import APIRouter, Body, Header, HTTPException, Query, status

from app.api.deps import CurrentIdentity, CurrentUser, DbSession
from app.modules.inventory.schemas import (
    AccessApplicationPage, AccessApplicationResponse, AccessApplicationReview,
    ApplicationStatus, EmptyRequest,
    StockMovementPage, StockOperationRequest, StockOperationResponse, StockPage, WarehouseResponse,
)
from app.services.identity import Role
from app.services.warehouse_access import (
    WarehouseAccessConflict, WarehouseAccessForbidden, WarehouseAccessIneligible,
    WarehouseAccessNotFound, WarehouseAccessService,
)
from app.services.inventory import InventoryConflict, InventoryForbidden, InventoryNotFound, InventoryService

router = APIRouter(prefix="/inventory", tags=["inventory"])


@router.post("/access-applications", response_model=AccessApplicationResponse, status_code=status.HTTP_201_CREATED)
def submit_access_application(
    user: CurrentUser,
    identity: CurrentIdentity,
    session: DbSession,
    _empty: EmptyRequest = Body(default=EmptyRequest()),
) -> AccessApplicationResponse:
    if identity.role is not Role.SHOP_OWNER:
        raise HTTPException(status_code=403, detail="current shop owner required")
    applicant_user_id = int(user.id)
    WarehouseAccessService.end_read_only_transaction(session)
    try:
        return WarehouseAccessService.submit(session, applicant_user_id=applicant_user_id)
    except WarehouseAccessForbidden as exc:
        raise HTTPException(status_code=403, detail="current shop owner required") from exc
    except WarehouseAccessConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except WarehouseAccessIneligible as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/access-applications", response_model=AccessApplicationPage)
def list_access_applications(
    user: CurrentUser,
    identity: CurrentIdentity,
    session: DbSession,
    status_filter: Annotated[ApplicationStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> AccessApplicationPage:
    try:
        return WarehouseAccessService.list_applications(
            session, actor_user_id=int(user.id), role=identity.role,
            merchant_id=identity.merchant_id, shop_id=identity.shop_id,
            status_filter=status_filter, limit=limit, offset=offset,
        )
    except WarehouseAccessForbidden as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/access-applications/{application_id}/review", response_model=AccessApplicationResponse)
def review_access_application(
    application_id: int,
    request: AccessApplicationReview,
    user: CurrentUser,
    identity: CurrentIdentity,
    session: DbSession,
) -> AccessApplicationResponse:
    # Check the role before looking up the ID so non-admins cannot probe records.
    if identity.role is not Role.MERCHANT_ADMIN or identity.merchant_id is None:
        raise HTTPException(status_code=403, detail="merchant administrator required")
    reviewer_user_id = int(user.id)
    try:
        applicant_user_id, shop_id = WarehouseAccessService.prepare_review(session, application_id)
    except WarehouseAccessNotFound as exc:
        raise HTTPException(status_code=404, detail="application not found") from exc
    WarehouseAccessService.end_read_only_transaction(session)
    try:
        return WarehouseAccessService.review(
            session, reviewer_user_id=reviewer_user_id, expected_merchant_id=identity.merchant_id,
            application_id=application_id, expected_applicant_user_id=applicant_user_id,
            expected_shop_id=shop_id, decision=request.decision, rejection_reason=request.rejection_reason,
        )
    except WarehouseAccessNotFound as exc:
        raise HTTPException(status_code=404, detail="application not found") from exc
    except WarehouseAccessForbidden as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except WarehouseAccessConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except WarehouseAccessIneligible as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/warehouse", response_model=WarehouseResponse)
def get_inventory_warehouse(user: CurrentUser, identity: CurrentIdentity, session: DbSession):
    try:
        return InventoryService.warehouse(session, identity=identity)
    except InventoryForbidden as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except InventoryNotFound as exc:
        raise HTTPException(status_code=404, detail="warehouse not found") from exc


@router.get("/stocks", response_model=StockPage)
def list_inventory_stocks(
    user: CurrentUser,
    identity: CurrentIdentity,
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
):
    try:
        return InventoryService.stocks(session, identity=identity, limit=limit, offset=offset)
    except InventoryForbidden as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/movements", response_model=StockMovementPage)
def list_inventory_movements(
    user: CurrentUser,
    identity: CurrentIdentity,
    session: DbSession,
    sku_id: Annotated[int | None, Query(gt=0)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
):
    if identity.role is not Role.MERCHANT_ADMIN:
        raise HTTPException(status_code=403, detail="merchant administrator required")
    try:
        return InventoryService.movements(session, identity=identity, sku_id=sku_id, limit=limit, offset=offset)
    except InventoryForbidden as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/operations", response_model=StockOperationResponse)
def adjust_inventory(
    request: StockOperationRequest,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    user: CurrentUser,
    identity: CurrentIdentity,
    session: DbSession,
):
    if identity.role is not Role.MERCHANT_ADMIN:
        raise HTTPException(status_code=403, detail="merchant administrator required")
    try:
        return InventoryService.adjust(
            session, actor_user_id=int(user.id), sku_id=request.sku_id,
            delta=request.delta, idempotency_key=idempotency_key,
        )
    except InventoryForbidden as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except InventoryNotFound as exc:
        raise HTTPException(status_code=404, detail="SKU not found") from exc
    except InventoryConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

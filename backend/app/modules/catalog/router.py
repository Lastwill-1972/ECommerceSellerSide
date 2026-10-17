from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import CurrentIdentity, CurrentUser, DbSession
from app.modules.catalog.schemas import (
    CategoryCreate, CategoryPage, CategoryResponse, SpuCreate, SpuDetail, SpuPage,
    SpuResponse, SpuStatus, SpuUpdate, SkuCreate, SkuResponse, SkuUpdate,
)
from app.services.catalog import CatalogConflict, CatalogForbidden, CatalogNotFound, CatalogService
from app.core.permissions import AccessDenied, AuthorizationService
from app.services.identity import Identity
from app.modules.catalog.offshelf_schemas import (
    OffshelfApplicationCreate, OffshelfApplicationPage, OffshelfApplicationResponse,
    OffshelfApplicationReview, OffshelfStatus,
)
from app.services.offshelf_applications import (
    OffshelfApplicationService, OffshelfConflict, OffshelfForbidden, OffshelfNotFound,
)

router = APIRouter(prefix="/catalog", tags=["catalog"])


def _require_writer(identity: Identity) -> None:
    try:
        AuthorizationService.catalog_merchant(identity, write=True)
    except AccessDenied as exc:
        raise HTTPException(status_code=403, detail="Insufficient permissions") from exc


def _end_read_transaction(session: Session) -> None:
    if session.new or session.dirty or session.deleted:
        raise RuntimeError("cannot discard a Session containing pending changes")
    if session.in_transaction():
        session.rollback()


def _translate(exc):
    if isinstance(exc, CatalogNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, CatalogForbidden):
        return HTTPException(status_code=403, detail="Insufficient permissions")
    if isinstance(exc, CatalogConflict):
        return HTTPException(status_code=409, detail=str(exc))
    raise exc


def _translate_offshelf(exc):
    if isinstance(exc, OffshelfNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, OffshelfForbidden):
        return HTTPException(status_code=403, detail="Insufficient permissions")
    if isinstance(exc, OffshelfConflict):
        return HTTPException(status_code=409, detail=str(exc))
    raise exc


@router.post("/offshelf-applications", response_model=OffshelfApplicationResponse, status_code=status.HTTP_201_CREATED)
def submit_offshelf_application(
    request: OffshelfApplicationCreate, user: CurrentUser, session: DbSession,
) -> OffshelfApplicationResponse:
    actor_user_id = int(user.id)
    _end_read_transaction(session)
    try:
        return OffshelfApplicationService.submit(session, actor_user_id=actor_user_id, spu_id=request.spu_id)
    except (OffshelfNotFound, OffshelfForbidden, OffshelfConflict) as exc:
        raise _translate_offshelf(exc)


@router.get("/offshelf-applications", response_model=OffshelfApplicationPage)
def list_offshelf_applications(
    identity: CurrentIdentity,
    session: DbSession,
    status_filter: Annotated[OffshelfStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> OffshelfApplicationPage:
    try:
        return OffshelfApplicationService.list(
            session, identity=identity, limit=limit, offset=offset, status_filter=status_filter,
        )
    except OffshelfForbidden as exc:
        raise _translate_offshelf(exc)


@router.post(
    "/offshelf-applications/{application_id}/review",
    response_model=OffshelfApplicationResponse,
)
def review_offshelf_application(
    application_id: int, request: OffshelfApplicationReview, user: CurrentUser,
    identity: CurrentIdentity, session: DbSession,
) -> OffshelfApplicationResponse:
    try:
        AuthorizationService.require_platform_admin(identity)
    except AccessDenied as exc:
        raise HTTPException(status_code=403, detail="Insufficient permissions") from exc
    reviewer_user_id = int(user.id)
    _end_read_transaction(session)
    try:
        return OffshelfApplicationService.review(
            session, reviewer_user_id=reviewer_user_id, application_id=application_id,
            decision=request.decision, rejection_reason=request.rejection_reason,
        )
    except (OffshelfNotFound, OffshelfForbidden, OffshelfConflict) as exc:
        raise _translate_offshelf(exc)


@router.get("/categories", response_model=CategoryPage)
def list_categories(
    identity: CurrentIdentity,
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> CategoryPage:
    try:
        return CatalogService.list_categories(session, identity=identity, limit=limit, offset=offset)
    except CatalogForbidden as exc:
        raise _translate(exc)


@router.post("/categories", response_model=CategoryResponse, status_code=status.HTTP_201_CREATED)
def create_category(
    request: CategoryCreate, user: CurrentUser, identity: CurrentIdentity, session: DbSession,
) -> CategoryResponse:
    _require_writer(identity)
    actor_user_id = int(user.id)
    _end_read_transaction(session)
    try:
        return CatalogService.create_category(session, actor_user_id=actor_user_id, name=request.name)
    except (CatalogConflict, CatalogForbidden) as exc:
        raise _translate(exc)


@router.get("/spus", response_model=SpuPage)
def list_spus(
    identity: CurrentIdentity,
    session: DbSession,
    category_id: Annotated[int | None, Query(gt=0)] = None,
    status_filter: Annotated[SpuStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> SpuPage:
    try:
        return CatalogService.list_spus(
            session, identity=identity, limit=limit, offset=offset, category_id=category_id,
            status_filter=status_filter,
        )
    except CatalogForbidden as exc:
        raise _translate(exc)


@router.post("/spus", response_model=SpuResponse, status_code=status.HTTP_201_CREATED)
def create_spu(
    request: SpuCreate, user: CurrentUser, identity: CurrentIdentity, session: DbSession,
) -> SpuResponse:
    _require_writer(identity)
    actor_user_id = int(user.id)
    _end_read_transaction(session)
    try:
        return CatalogService.create_spu(
            session, actor_user_id=actor_user_id, category_id=request.category_id, name=request.name,
            description=request.description,
        )
    except (CatalogNotFound, CatalogForbidden) as exc:
        raise _translate(exc)


@router.get("/spus/{spu_id}", response_model=SpuDetail)
def get_spu(spu_id: int, identity: CurrentIdentity, session: DbSession) -> SpuDetail:
    try:
        return CatalogService.get_spu(session, identity=identity, spu_id=spu_id)
    except (CatalogForbidden, CatalogNotFound) as exc:
        raise _translate(exc)


@router.patch("/spus/{spu_id}", response_model=SpuResponse)
def update_spu(
    spu_id: int, request: SpuUpdate, user: CurrentUser, identity: CurrentIdentity, session: DbSession,
) -> SpuResponse:
    _require_writer(identity)
    actor_user_id = int(user.id)
    changes = request.model_dump(exclude_unset=True)
    _end_read_transaction(session)
    try:
        return CatalogService.update_spu(session, actor_user_id=actor_user_id, spu_id=spu_id, changes=changes)
    except (CatalogNotFound, CatalogForbidden, CatalogConflict) as exc:
        raise _translate(exc)


@router.patch("/spus/{spu_id}/publish", response_model=SpuResponse)
def publish_spu(spu_id: int, user: CurrentUser, identity: CurrentIdentity, session: DbSession) -> SpuResponse:
    _require_writer(identity)
    actor_user_id = int(user.id)
    _end_read_transaction(session)
    try:
        return CatalogService.publish_spu(session, actor_user_id=actor_user_id, spu_id=spu_id)
    except (CatalogNotFound, CatalogForbidden, CatalogConflict) as exc:
        raise _translate(exc)


@router.post("/spus/{spu_id}/skus", response_model=SkuResponse, status_code=status.HTTP_201_CREATED)
def create_sku(
    spu_id: int, request: SkuCreate, user: CurrentUser, identity: CurrentIdentity, session: DbSession,
) -> SkuResponse:
    _require_writer(identity)
    actor_user_id = int(user.id)
    _end_read_transaction(session)
    try:
        return CatalogService.create_sku(
            session, actor_user_id=actor_user_id, spu_id=spu_id, sku_code=request.sku_code,
            specification=request.specification, price=request.price,
        )
    except (CatalogNotFound, CatalogForbidden, CatalogConflict) as exc:
        raise _translate(exc)


@router.patch("/skus/{sku_id}", response_model=SkuResponse)
def update_sku(
    sku_id: int, request: SkuUpdate, user: CurrentUser, identity: CurrentIdentity, session: DbSession,
) -> SkuResponse:
    _require_writer(identity)
    actor_user_id = int(user.id)
    changes = request.model_dump(exclude_unset=True)
    _end_read_transaction(session)
    try:
        return CatalogService.update_sku(session, actor_user_id=actor_user_id, sku_id=sku_id, changes=changes)
    except (CatalogNotFound, CatalogForbidden, CatalogConflict) as exc:
        raise _translate(exc)

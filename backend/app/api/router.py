from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from app.api.health import router as health_router
from app.api.deps import CurrentIdentity, CurrentUser, DbSession
from app.core.audit_schemas import AuditEventPage
from app.services.audit_query import AuditQueryService
from app.services.identity import Role
from app.modules.auth.router import router as auth_router
from app.modules.catalog.router import router as catalog_router
from app.modules.employees.router import router as employees_router
from app.modules.inventory.router import router as inventory_router
from app.modules.merchants.router import router as merchants_router
from app.modules.orders.router import router as orders_router
from app.modules.shops.router import router as shops_router
from app.modules.users.router import router as users_router

api_router = APIRouter()
api_router.include_router(health_router)


@api_router.get("/audit/events", response_model=AuditEventPage, tags=["audit"])
def list_audit_events(
    _user: CurrentUser,
    identity: CurrentIdentity,
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
    event_type: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    target_type: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
) -> AuditEventPage:
    # Audit timestamps are MySQL DATETIME values written in the server's local
    # timezone and returned without tzinfo. Converting an aware value to UTC-naive
    # would silently compare different wall-clock domains, so require naive filters.
    if (start_at is not None and start_at.tzinfo is not None) or (
        end_at is not None and end_at.tzinfo is not None
    ):
        raise HTTPException(status_code=422, detail="timezone-aware filters are unsupported; use naive database-local times")
    normalized_start, normalized_end = start_at, end_at
    if normalized_start is not None and normalized_end is not None and normalized_start > normalized_end:
        raise HTTPException(status_code=422, detail="start_at must be earlier than or equal to end_at")
    if identity.role not in (Role.PLATFORM_ADMIN, Role.MERCHANT_ADMIN, Role.SHOP_OWNER):
        raise HTTPException(status_code=403, detail="audit query is not available for this identity")
    return AuditQueryService.list_events(session, identity=identity, limit=limit, offset=offset,
                                         event_type=event_type, start_at=normalized_start, end_at=normalized_end,
                                         target_type=target_type)
for business_router in (
    auth_router, users_router, merchants_router, shops_router,
    employees_router, catalog_router, inventory_router, orders_router,
):
    api_router.include_router(business_router)

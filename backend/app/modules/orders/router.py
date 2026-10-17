from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, status

from app.api.deps import CurrentIdentity, CurrentUser, DbSession
from app.modules.orders.schemas import OrderCreate, OrderPage, OrderResponse
from app.services.identity import Role
from app.services.orders import OrderConflict, OrderForbidden, OrderNotFound, OrderService
from app.services.inventory import InventoryConflict

router = APIRouter(prefix="/orders", tags=["orders"])


@router.post("", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
def create_order(
    request: OrderCreate,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    user: CurrentUser,
    _identity: CurrentIdentity,
    session: DbSession,
):
    try:
        return OrderService.create(session, actor_user_id=int(user.id), items=request.items,
                                   idempotency_key=idempotency_key)
    except (InventoryConflict, OrderConflict) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OrderForbidden as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except OrderNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("", response_model=OrderPage)
def list_orders(
    user: CurrentUser,
    identity: CurrentIdentity,
    session: DbSession,
    shop_id: Annotated[int | None, Query(gt=0)] = None,
    status_filter: Annotated[str | None, Query(alias="status", pattern="^(PENDING|COMPLETED|CANCELLED)$")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
):
    try:
        return OrderService.list_orders(session, identity=identity, shop_id=shop_id,
                                        status_filter=status_filter, limit=limit, offset=offset)
    except OrderForbidden as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/{order_id}", response_model=OrderResponse)
def get_order(order_id: int, user: CurrentUser, identity: CurrentIdentity, session: DbSession):
    try:
        return OrderService.get_order(session, identity=identity, order_id=order_id)
    except OrderNotFound as exc:
        raise HTTPException(status_code=404, detail="order not found") from exc


@router.post("/{order_id}/complete", response_model=OrderResponse)
def complete_order(order_id: int, user: CurrentUser, _identity: CurrentIdentity, session: DbSession):
    if _identity.role not in (Role.SHOP_OWNER, Role.SHOP_EMPLOYEE) or _identity.shop_id is None:
        raise HTTPException(status_code=403, detail="shop owner or employee required")
    try:
        return OrderService.complete(session, actor_user_id=int(user.id), order_id=order_id,
                                     expected_shop_id=int(_identity.shop_id))
    except OrderForbidden as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except OrderNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except OrderConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except InventoryConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{order_id}/cancel", response_model=OrderResponse)
def cancel_order(order_id: int, user: CurrentUser, _identity: CurrentIdentity, session: DbSession):
    if _identity.role not in (Role.SHOP_OWNER, Role.SHOP_EMPLOYEE) or _identity.shop_id is None:
        raise HTTPException(status_code=403, detail="shop owner or employee required")
    try:
        return OrderService.cancel(session, actor_user_id=int(user.id), order_id=order_id,
                                   expected_shop_id=int(_identity.shop_id))
    except OrderForbidden as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except OrderNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except OrderConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except InventoryConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


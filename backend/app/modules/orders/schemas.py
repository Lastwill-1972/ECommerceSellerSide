from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_serializer


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OrderItemCreate(StrictRequest):
    sku_id: Annotated[StrictInt, Field(gt=0, le=9_223_372_036_854_775_807)]
    quantity: Annotated[StrictInt, Field(gt=0, le=9_223_372_036_854_775_807)]


class OrderCreate(StrictRequest):
    items: Annotated[list[OrderItemCreate], Field(min_length=1, max_length=100)]


class OrderItemResponse(BaseModel):
    id: int
    sku_id: int
    quantity: int
    unit_price: Decimal
    subtotal: Decimal
    sku_code_snapshot: str
    spu_name_snapshot: str
    specification_snapshot: str

    @field_serializer("unit_price", "subtotal")
    def serialize_amount(self, value: Decimal) -> str:
        return format(value, ".2f")


class OrderResponse(BaseModel):
    id: int
    merchant_id: int
    shop_id: int
    created_by_user_id: int
    status: str
    total_amount: Decimal
    created_at: datetime
    completed_at: datetime | None
    cancelled_at: datetime | None
    items: list[OrderItemResponse]

    @field_serializer("total_amount")
    def serialize_amount(self, value: Decimal) -> str:
        return format(value, ".2f")


class OrderPage(BaseModel):
    items: list[OrderResponse]
    limit: int
    offset: int
    total: int


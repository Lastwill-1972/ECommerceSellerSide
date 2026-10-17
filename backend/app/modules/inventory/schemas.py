from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator


class ApplicationStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ApplicationDecision(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmptyRequest(StrictRequest):
    pass


class AccessApplicationReview(StrictRequest):
    decision: ApplicationDecision
    rejection_reason: Annotated[str | None, Field(max_length=2000)] = None

    @model_validator(mode="after")
    def validate_rejection_reason(self):
        if self.decision is ApplicationDecision.REJECT:
            if self.rejection_reason is None or not self.rejection_reason.strip():
                raise ValueError("rejection_reason is required when rejecting")
        elif self.rejection_reason is not None:
            raise ValueError("rejection_reason is only allowed when rejecting")
        return self


class AccessApplicationResponse(BaseModel):
    id: int
    shop_id: int
    applicant_user_id: int
    status: ApplicationStatus
    reviewer_user_id: int | None
    rejection_reason: str | None
    created_at: datetime
    reviewed_at: datetime | None


class AccessApplicationPage(BaseModel):
    items: list[AccessApplicationResponse]
    limit: int
    offset: int
    total: int


class StockOperationRequest(StrictRequest):
    sku_id: StrictInt = Field(gt=0, le=9_223_372_036_854_775_807)
    delta: StrictInt = Field(ge=-9_223_372_036_854_775_808, le=9_223_372_036_854_775_807)

    @model_validator(mode="after")
    def nonzero_delta(self):
        if self.delta == 0:
            raise ValueError("delta must not be zero")
        return self


class StockResponse(BaseModel):
    sku_id: int
    sku_code: str
    specification: str
    spu_id: int
    spu_name: str
    warehouse_id: int
    quantity: int


class StockPage(BaseModel):
    items: list[StockResponse]
    limit: int
    offset: int
    total: int


class StockOperationResponse(BaseModel):
    movement_id: int
    sku_id: int
    quantity_before: int
    quantity_delta: int
    quantity_after: int
    idempotency_key: str


class StockMovementResponse(StockOperationResponse):
    idempotency_key: str | None
    merchant_id: int
    warehouse_id: int
    actor_user_id: int
    operation_type: str
    order_item_id: int | None
    created_at: datetime


class StockMovementPage(BaseModel):
    items: list[StockMovementResponse]
    limit: int
    offset: int
    total: int


class WarehouseResponse(BaseModel):
    id: int
    merchant_id: int
    name: str
    created_at: datetime

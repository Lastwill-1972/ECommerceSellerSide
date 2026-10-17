from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Union
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ShopApplicationStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ShopApplicationDecision(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OwnerTransferPropose(StrictRequest):
    action: Literal["PROPOSE"]
    target_user_id: Annotated[int, Field(gt=0)]


class OwnerTransferConfirm(StrictRequest):
    action: Literal["CONFIRM"]
    proposal_id: UUID


class OwnerTransferForce(StrictRequest):
    action: Literal["FORCE"]
    target_user_id: Annotated[int, Field(gt=0)]
    reason: Annotated[str, Field(min_length=1, max_length=2000)]

    @model_validator(mode="before")
    @classmethod
    def trim_reason(cls, value):
        if isinstance(value, dict) and isinstance(value.get("reason"), str):
            value = {**value, "reason": value["reason"].strip()}
        return value


OwnerTransferRequest = Annotated[
    Union[OwnerTransferPropose, OwnerTransferConfirm, OwnerTransferForce], Field(discriminator="action")
]


class OwnerTransferResponse(BaseModel):
    shop_id: int
    owner_user_id: int
    status: Literal["PROPOSED", "CONFIRMED", "FORCED"]
    proposal_id: UUID | None = None
    target_user_id: int | None = None


class ShopApplicationCreate(StrictRequest):
    merchant_id: Annotated[int, Field(gt=0)]
    shop_name: Annotated[str, Field(min_length=1, max_length=255)]

    @model_validator(mode="before")
    @classmethod
    def trim_name(cls, value):
        if isinstance(value, dict) and isinstance(value.get("shop_name"), str):
            value = {**value, "shop_name": value["shop_name"].strip()}
        return value


class ShopApplicationReview(StrictRequest):
    decision: ShopApplicationDecision
    rejection_reason: Annotated[str | None, Field(max_length=2000)] = None

    @model_validator(mode="after")
    def validate_rejection_reason(self):
        if self.decision is ShopApplicationDecision.REJECT:
            if self.rejection_reason is None or not self.rejection_reason.strip():
                raise ValueError("rejection_reason is required when rejecting")
        elif self.rejection_reason is not None:
            raise ValueError("rejection_reason is only allowed when rejecting")
        return self


class ShopApplicationResponse(BaseModel):
    id: int
    merchant_id: int
    applicant_user_id: int
    shop_name: str
    status: ShopApplicationStatus
    reviewer_user_id: int | None
    rejection_reason: str | None
    created_shop_id: int | None
    created_at: datetime
    reviewed_at: datetime | None


class ShopApplicationPage(BaseModel):
    items: list[ShopApplicationResponse]
    limit: int
    offset: int
    total: int


class ShopResponse(BaseModel):
    id: int
    merchant_id: int
    name: str
    owner_user_id: int
    created_at: datetime


class ShopPage(BaseModel):
    items: list[ShopResponse]
    limit: int
    offset: int
    total: int

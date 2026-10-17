from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ApplicationStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ApplicationDecision(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MerchantProfileUpdate(StrictRequest):
    name: Annotated[str, Field(min_length=1, max_length=255)]

    @model_validator(mode="before")
    @classmethod
    def trim_name(cls, value):
        if isinstance(value, dict) and isinstance(value.get("name"), str):
            value = {**value, "name": value["name"].strip()}
        return value


class MerchantProfileResponse(BaseModel):
    id: int
    name: str


class MerchantApplicationCreate(StrictRequest):
    merchant_name: Annotated[str, Field(min_length=1, max_length=255)]

    @model_validator(mode="before")
    @classmethod
    def trim_name(cls, value):
        if isinstance(value, dict) and isinstance(value.get("merchant_name"), str):
            value = {**value, "merchant_name": value["merchant_name"].strip()}
        return value


class MerchantApplicationReview(StrictRequest):
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


class MerchantApplicationResponse(BaseModel):
    id: int
    applicant_user_id: int
    merchant_name: str
    status: ApplicationStatus
    reviewer_user_id: int | None
    rejection_reason: str | None
    created_merchant_id: int | None
    created_at: datetime
    reviewed_at: datetime | None


class MerchantApplicationPage(BaseModel):
    items: list[MerchantApplicationResponse]
    limit: int
    offset: int
    total: int


class MerchantDirectoryItem(BaseModel):
    id: int
    name: str


class MerchantDirectoryPage(BaseModel):
    items: list[MerchantDirectoryItem]
    limit: int
    offset: int
    total: int

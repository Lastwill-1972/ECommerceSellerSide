from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator


class OffshelfDecision(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


class OffshelfStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class OffshelfApplicationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spu_id: Annotated[int, Field(gt=0)]


class OffshelfApplicationReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: OffshelfDecision
    rejection_reason: Annotated[str, Field(min_length=1, max_length=2000)] | None = None

    @model_validator(mode="before")
    @classmethod
    def trim_reason(cls, value):
        if isinstance(value, dict) and isinstance(value.get("rejection_reason"), str):
            value = {**value, "rejection_reason": value["rejection_reason"].strip()}
        return value

    @model_validator(mode="after")
    def validate_decision_reason(self):
        if self.decision is OffshelfDecision.REJECT and not self.rejection_reason:
            raise ValueError("rejection_reason is required for REJECT")
        if self.decision is OffshelfDecision.APPROVE and self.rejection_reason is not None:
            raise ValueError("rejection_reason is not allowed for APPROVE")
        return self


class OffshelfSpuSummary(BaseModel):
    id: int
    name: str
    status: str
    merchant_id: int


class OffshelfApplicationResponse(BaseModel):
    id: int
    spu_id: int
    applicant_user_id: int
    status: str
    reviewer_user_id: int | None
    rejection_reason: str | None
    created_at: datetime
    reviewed_at: datetime | None
    spu: OffshelfSpuSummary


class OffshelfApplicationPage(BaseModel):
    items: list[OffshelfApplicationResponse]
    limit: int
    offset: int
    total: int

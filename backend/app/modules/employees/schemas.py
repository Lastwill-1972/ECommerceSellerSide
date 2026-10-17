from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EmployeeApplicationStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class EmployeeApplicationDecision(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmployeeApplicationCreate(StrictRequest):
    shop_id: Annotated[int, Field(gt=0)]


class EmployeeApplicationReview(StrictRequest):
    decision: EmployeeApplicationDecision
    rejection_reason: Annotated[str | None, Field(max_length=2000)] = None

    @model_validator(mode="after")
    def validate_rejection_reason(self):
        if self.decision is EmployeeApplicationDecision.REJECT:
            if self.rejection_reason is None or not self.rejection_reason.strip():
                raise ValueError("rejection_reason is required when rejecting")
        elif self.rejection_reason is not None:
            raise ValueError("rejection_reason is only allowed when rejecting")
        return self


class EmployeeApplicationResponse(BaseModel):
    id: int
    shop_id: int
    applicant_user_id: int
    status: EmployeeApplicationStatus
    reviewer_user_id: int | None
    rejection_reason: str | None
    created_at: datetime
    reviewed_at: datetime | None


class EmployeeApplicationPage(BaseModel):
    items: list[EmployeeApplicationResponse]
    limit: int
    offset: int
    total: int


class ShopEmployeeResponse(BaseModel):
    id: int
    shop_id: int
    user_id: int
    role_id: int
    role_name: str
    joined_at: datetime


class ShopEmployeePage(BaseModel):
    items: list[ShopEmployeeResponse]
    limit: int
    offset: int
    total: int


class ShopEmployeeRoleCreate(StrictRequest):
    name: Annotated[str, Field(min_length=1, max_length=100)]

    @model_validator(mode="before")
    @classmethod
    def trim_name(cls, value):
        if isinstance(value, dict) and isinstance(value.get("name"), str):
            value = {**value, "name": value["name"].strip()}
        return value


class ShopEmployeeRoleResponse(BaseModel):
    id: int
    shop_id: int
    name: str
    created_at: datetime


class ShopEmployeeRolePage(BaseModel):
    items: list[ShopEmployeeRoleResponse]
    limit: int
    offset: int
    total: int


class ShopEmployeeRoleUpdate(StrictRequest):
    role_id: Annotated[int, Field(gt=0)]

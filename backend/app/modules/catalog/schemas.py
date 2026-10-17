from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator


class SpuStatus(StrEnum):
    OFF_SALE = "OFF_SALE"
    ON_SALE = "ON_SALE"


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CategoryCreate(StrictRequest):
    name: Annotated[str, Field(min_length=1, max_length=255)]

    @model_validator(mode="before")
    @classmethod
    def trim_name(cls, value):
        if isinstance(value, dict) and isinstance(value.get("name"), str):
            value = {**value, "name": value["name"].strip()}
        return value


class CategoryResponse(BaseModel):
    id: int
    merchant_id: int
    name: str
    created_at: datetime


class CategoryPage(BaseModel):
    items: list[CategoryResponse]
    limit: int
    offset: int
    total: int


class SpuCreate(StrictRequest):
    category_id: Annotated[int, Field(gt=0)]
    name: Annotated[str, Field(min_length=1, max_length=255)]
    description: Annotated[str | None, Field(max_length=10000)] = None

    @model_validator(mode="before")
    @classmethod
    def trim_name(cls, value):
        if isinstance(value, dict) and isinstance(value.get("name"), str):
            value = {**value, "name": value["name"].strip()}
        return value


class SpuUpdate(StrictRequest):
    category_id: Annotated[int, Field(gt=0)] | None = None
    name: Annotated[str, Field(min_length=1, max_length=255)] | None = None
    description: Annotated[str | None, Field(max_length=10000)] = None

    @model_validator(mode="before")
    @classmethod
    def trim_name(cls, value):
        if isinstance(value, dict) and isinstance(value.get("name"), str):
            value = {**value, "name": value["name"].strip()}
        return value

    @model_validator(mode="after")
    def require_change(self):
        if not self.model_fields_set:
            raise ValueError("at least one field is required")
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("name cannot be null")
        if "category_id" in self.model_fields_set and self.category_id is None:
            raise ValueError("category_id cannot be null")
        return self


class SpuResponse(BaseModel):
    id: int
    merchant_id: int
    category_id: int
    name: str
    description: str | None
    status: SpuStatus
    created_at: datetime
    updated_at: datetime


class SpuDetail(SpuResponse):
    skus: list["SkuResponse"]


class SpuPage(BaseModel):
    items: list[SpuResponse]
    limit: int
    offset: int
    total: int


class SkuCreate(StrictRequest):
    sku_code: Annotated[str, Field(min_length=1, max_length=100)]
    specification: Annotated[str, Field(min_length=1, max_length=1000)]
    price: Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)]

    @model_validator(mode="before")
    @classmethod
    def trim_text(cls, value):
        if isinstance(value, dict):
            value = dict(value)
            for key in ("sku_code", "specification"):
                if isinstance(value.get(key), str):
                    value[key] = value[key].strip()
        return value

    @model_validator(mode="after")
    def finite_price(self):
        if not self.price.is_finite():
            raise ValueError("price must be finite")
        return self


class SkuUpdate(StrictRequest):
    sku_code: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    specification: Annotated[str, Field(min_length=1, max_length=1000)] | None = None
    price: Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)] | None = None

    @model_validator(mode="before")
    @classmethod
    def trim_text(cls, value):
        if isinstance(value, dict):
            value = dict(value)
            for key in ("sku_code", "specification"):
                if isinstance(value.get(key), str):
                    value[key] = value[key].strip()
        return value

    @model_validator(mode="after")
    def validate_change(self):
        if not self.model_fields_set:
            raise ValueError("at least one field is required")
        if any(getattr(self, key) is None for key in self.model_fields_set):
            raise ValueError("SKU fields cannot be null")
        if self.price is not None and not self.price.is_finite():
            raise ValueError("price must be finite")
        return self


class SkuResponse(BaseModel):
    id: int
    spu_id: int
    sku_code: str
    specification: str
    price: Decimal
    created_at: datetime
    updated_at: datetime

    @field_serializer("price")
    def serialize_price(self, value: Decimal) -> str:
        return format(value, ".2f")



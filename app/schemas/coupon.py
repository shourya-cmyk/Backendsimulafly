import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CouponCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    code: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=255)
    discount_type: Literal["flat", "percentage"] = "flat"
    discount_value: Decimal = Field(gt=0)
    min_order_amount: Decimal = Field(default=Decimal("0.0"), ge=0)
    max_discount_amount: Decimal | None = Field(default=None, gt=0)
    usage_limit: int | None = Field(default=None, ge=1)
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def validate_percentage(self) -> "CouponCreate":
        if self.discount_type == "percentage" and self.discount_value > 100:
            raise ValueError("percentage discount_value must not exceed 100")
        return self


class CouponValidateRequest(BaseModel):
    code: str
    merchant_id: uuid.UUID
    order_amount: Decimal = Field(default=Decimal("0.0"), ge=0)


class CouponValidateResponse(BaseModel):
    valid: bool
    code: str
    discount_type: str | None = None
    discount_value: float | None = None
    discount_amount: float = 0.0
    title: str | None = None
    reason: str | None = None


class CouponOut(BaseModel):
    id: uuid.UUID
    merchant_id: uuid.UUID
    code: str
    title: str
    discount_type: str
    discount_value: float
    min_order_amount: float
    max_discount_amount: float | None = None
    usage_limit: int | None = None
    used_count: int
    is_active: bool
    expires_at: datetime | None = None

    class Config:
        from_attributes = True

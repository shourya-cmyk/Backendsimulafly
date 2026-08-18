import uuid
from decimal import Decimal

from pydantic import BaseModel, Field


class CouponCreate(BaseModel):
    code: str = Field(..., max_length=64)
    title: str = Field(..., max_length=255)
    discount_type: str = Field(default="flat")  # "flat" or "percentage"
    discount_value: Decimal
    min_order_amount: Decimal = Decimal("0.0")
    max_discount_amount: Decimal | None = None
    usage_limit: int | None = None
    merchant_id: uuid.UUID | None = None


class CouponValidateRequest(BaseModel):
    code: str
    merchant_id: uuid.UUID | None = None
    order_amount: Decimal = Decimal("0.0")


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
    merchant_id: uuid.UUID | None = None
    code: str
    title: str
    discount_type: str
    discount_value: float
    min_order_amount: float
    is_active: bool

    class Config:
        from_attributes = True

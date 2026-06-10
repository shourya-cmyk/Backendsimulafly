import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.product import MerchantProductSimpleOut


class CartItemAdd(BaseModel):
    product_id: uuid.UUID  # merchant_product id
    quantity: int = Field(default=1, ge=1, le=10)


class CartItemUpdate(BaseModel):
    quantity: int = Field(ge=1, le=10)


class CartItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID  # alias for merchant_product_id — kept for API compat
    quantity: int
    added_at: datetime
    product: MerchantProductSimpleOut


class CartSummary(BaseModel):
    items: list[CartItemOut]
    estimated_total: float
    currency: str = "INR"
    item_count: int

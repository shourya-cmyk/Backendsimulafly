import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


# ── Buyer-facing input ───────────────────────────────────────────────────────

class OrderItemIn(BaseModel):
    """Single line-item in a buyer lead submission."""
    product_id: uuid.UUID
    variant_id: uuid.UUID | None = None
    qty: int = Field(default=1, ge=1)
    price_at_capture: Decimal
    title: str
    img_url: str | None = None
    sku: str


class BuyerLeadCreate(BaseModel):
    """Flutter submits this to POST /buyer/leads/."""
    merchant_product_id: uuid.UUID
    session_id: str | None = None
    delivery_city: str | None = Field(default=None, max_length=100)
    delivery_phone: str | None = Field(default=None, max_length=50)
    items: list[OrderItemIn] = Field(default_factory=list)


# ── Merchant-facing input ────────────────────────────────────────────────────

class BuyerLeadUpdate(BaseModel):
    """Merchant PATCH — update status and/or notes."""
    status: str | None = None   # new|synced|converted|lost
    merchant_notes: str | None = None


# ── Shared output types ───────────────────────────────────────────────────────

class CustomerInfo(BaseModel):
    """Buyer contact info; PII fields are None when status is 'new'."""
    city: str | None = None
    name: str | None = None
    email: str | None = None
    phone: str | None = None


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: str
    items: list[dict]
    total_estimated: Decimal
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class BuyerLeadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    merchant_id: uuid.UUID
    lead_type: str
    status: str
    estimated_value: Decimal
    ai_interactions_count: int
    ai_generated_image_url: str | None = None
    delivery_city: str | None = None
    merchant_notes: str | None = None
    converted_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    # Joined at query time — not ORM-native
    customer: CustomerInfo
    order: OrderOut | None = None


class PaginatedLeads(BaseModel):
    items: list[BuyerLeadOut]
    total: int
    limit: int
    offset: int

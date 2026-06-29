"""Pydantic schemas for the merchant-facing support ticket API."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SupportTicketCreate(BaseModel):
    """Payload for ``POST /merchant/support/tickets``."""

    reason: str = Field(min_length=1, max_length=64, description="Parent category slug")
    sub_reason: str = Field(min_length=1, max_length=64, description="Child category slug")
    description: str = Field(min_length=1, max_length=4000, description="Detailed description")
    merchant_product_id: uuid.UUID | None = Field(
        default=None, description="Optional related product"
    )
    attachment_url: str | None = Field(
        default=None, max_length=2048, description="Optional screenshot / attachment URL"
    )


class SupportTicketOut(BaseModel):
    """Response for a merchant-submitted support ticket."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    subject: str
    status: str
    priority: str
    reason: str | None
    sub_reason: str | None
    merchant_product_id: uuid.UUID | None
    attachment_url: str | None
    description: str | None
    sla_due_at: datetime | None
    created_at: datetime
    updated_at: datetime


class PaginatedSupportTickets(BaseModel):
    items: list[SupportTicketOut]
    total: int
    limit: int
    offset: int

"""Request/response schemas for admin Booking operations (Requirement 11).

Bookings map onto the existing :class:`app.models.lead.Order` model (extended
with additive nullable columns for dispute/fulfillment tracking). The listing
reuses the shared :class:`ListingEnvelope` so the Admin Panel consumes it with
the same pagination shape as every other directory.

Booking line items are surfaced from the existing ``Order.items`` JSONB array;
each entry is shaped roughly like
``{product_id, variant_id, qty, price_at_capture, title, img_url, sku}`` but the
schema is permissive so historical/partial rows are not rejected.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.models.lead import DisputeStatus, FulfillmentStatus
from app.schemas.admin.listing import ListingEnvelope


class BookingLineItem(BaseModel):
    """A single line item drawn from the ``Order.items`` JSONB array (R11.3).

    All fields are optional because ``items`` is free-form JSON written by the
    consumer app; only the keys present on a given row are populated.
    """

    model_config = ConfigDict(extra="allow")

    product_id: str | None = None
    variant_id: str | None = None
    qty: int | None = None
    price_at_capture: Decimal | None = None
    title: str | None = None
    img_url: str | None = None
    sku: str | None = None


class BookingListItem(BaseModel):
    """A single row in the booking listing (R11.1).

    Carries the booking ``id`` (identifier), the ``customer`` (``user_id`` plus
    resolved ``customer_name``), the ``merchant`` (``merchant_id`` plus resolved
    ``merchant_name``), ``status``, ``amount`` (``total_estimated``), the
    ``created_at`` creation date, and the derived ``dispute_status`` /
    ``fulfillment_status`` markers.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    customer_name: str | None = None
    merchant_id: uuid.UUID
    merchant_name: str | None = None
    status: str
    amount: Decimal
    dispute_status: str
    fulfillment_status: str
    created_at: datetime


class BookingDetail(BookingListItem):
    """Detail record for a single booking (R11.3).

    Extends the list item with the ``line_items`` (from ``Order.items``), the
    ``delivery_address`` blob, dispute context (``dispute_reason``,
    ``dispute_resolution``), the linked ``lead_id``, merchant notes, and
    completion/update timestamps.
    """

    lead_id: uuid.UUID
    line_items: list[BookingLineItem]
    delivery_address: dict
    dispute_reason: str | None = None
    dispute_resolution: str | None = None
    merchant_notes: str | None = None
    completed_at: datetime | None = None
    updated_at: datetime


#: Pagination envelope for the booking listings (R11.1, R11.4, R11.6).
BookingListResponse = ListingEnvelope[BookingListItem]


class DisputeResolveRequest(BaseModel):
    """Payload for ``POST /admin/bookings/{id}/dispute/resolve`` (R11.5).

    ``resolution`` is the free-text outcome recorded against the booking.
    ``status`` is the new dispute status and defaults to ``resolved``; only
    valid :class:`DisputeStatus` values are accepted (others → HTTP 422).
    """

    resolution: str
    status: DisputeStatus = DisputeStatus.RESOLVED


class DisputeResolveResponse(BaseModel):
    """Result of resolving a booking dispute (R11.5)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    dispute_status: str
    dispute_resolution: str | None = None


class FulfillmentUpdateRequest(BaseModel):
    """Payload for ``PATCH /admin/bookings/{id}/fulfillment`` (R11.7).

    ``status`` must be a valid :class:`FulfillmentStatus` value
    (``pending|in_progress|fulfilled|cancelled``); any other value → HTTP 422.
    """

    status: FulfillmentStatus


class FulfillmentUpdateResponse(BaseModel):
    """Result of a booking fulfillment-status transition (R11.7)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    fulfillment_status: str

"""Request/response schemas for the admin Store directory (Requirement 9).

The Store directory exposes a paginated/searchable/sortable listing, a detail
view, and a status-change action over the :class:`app.models.store.Store`
entity. The listing reuses the shared :class:`ListingEnvelope` so the Admin
Panel consumes it with the same pagination shape as every other directory.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.store import StoreStatus
from app.schemas.admin.listing import ListingEnvelope


class StoreListItem(BaseModel):
    """A single row in the store directory listing (R9.1).

    Carries the store ``id`` (identifier), ``name``, the owning merchant
    (``merchant_id`` plus its display ``merchant_name``), ``status``, and the
    ``created_at`` creation date.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    merchant_id: uuid.UUID
    merchant_name: str | None = None
    status: str
    created_at: datetime


class StoreDetail(StoreListItem):
    """Detail record for a single store (R9.4).

    Extends the list item with the store ``settings`` blob and the
    ``updated_at`` timestamp.
    """

    settings: dict
    updated_at: datetime


#: Pagination envelope for ``GET /admin/stores`` (R9.1).
StoreListResponse = ListingEnvelope[StoreListItem]


class StoreStatusUpdateRequest(BaseModel):
    """Payload for ``PATCH /admin/stores/{id}/status`` (R9.5).

    ``status`` must be a valid :class:`StoreStatus` value
    (``active|inactive|suspended``); any other value is rejected with HTTP 422.
    """

    status: StoreStatus


class StoreStatusResponse(BaseModel):
    """Result of a store status transition (R9.5)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    merchant_id: uuid.UUID
    status: str

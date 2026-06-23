"""Request/response schemas for the admin redeem-code endpoints (Requirement 15).

These back the redeem-code router (``app/routers/admin/redeem_codes.py``):

* :class:`RedeemCodeGenerateRequest` — the ``{value, quantity, expiry}`` body of
  a batch generation request (R15.1). Pydantic validation enforces
  ``quantity >= 1`` and ``value > 0`` so out-of-range parameters are rejected
  with HTTP 422 before any code is created (R15.5).
* :class:`RedeemCodeItem` — one row in the paginated redeem-code listing (R15.2),
  wrapped by the shared :class:`app.schemas.admin.listing.ListingEnvelope`.
* :class:`RedeemCodeGenerateResponse` — the result of a generation request: the
  batch identifier and the created codes (R15.1).

All monetary values are denominated in INR, consistent with the existing
``RedeemCode`` model.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class RedeemCodeGenerateRequest(BaseModel):
    """Body of a redeem-code batch generation request (R15.1).

    ``quantity`` must be at least one and ``value`` strictly positive; a
    quantity below one or a non-positive value is rejected with HTTP 422 by
    request validation (R15.5). ``expiry`` is optional — when omitted the codes
    never expire.
    """

    value: Decimal = Field(gt=0, description="Positive INR value granted by each code")
    quantity: int = Field(ge=1, description="Number of unique codes to generate")
    expiry: datetime | None = Field(
        default=None, description="Optional expiry timestamp shared by every code in the batch"
    )


class RedeemCodeItem(BaseModel):
    """A single row in the paginated redeem-code directory (R15.2).

    Includes the code identifier, the code string, value, currency, status,
    expiry, and redemption details (who redeemed it and when).
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    value: Decimal
    currency: str
    status: str
    expires_at: datetime | None = None
    redeemed_by: uuid.UUID | None = None
    redeemed_at: datetime | None = None
    batch_id: uuid.UUID | None = None
    created_at: datetime


class RedeemCodeGenerateResponse(BaseModel):
    """Result of a redeem-code batch generation (R15.1)."""

    batch_id: uuid.UUID
    quantity: int
    items: list[RedeemCodeItem]

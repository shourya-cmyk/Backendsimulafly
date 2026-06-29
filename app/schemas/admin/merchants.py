"""Request/response schemas for the admin merchant directory (Requirement 8).

These back the merchant directory router (``app/routers/admin/merchants.py``):

* :class:`MerchantListItem` — one row in the paginated listing (R8.1), wrapped
  by the shared :class:`app.schemas.admin.listing.ListingEnvelope`.
* :class:`MerchantDetail` — the single-merchant detail record including members
  and a wallet summary (R8.4).
* :class:`MerchantStatusResponse` — the result of a suspend/activate transition
  (R8.5, R8.6).

All monetary values are denominated in INR, consistent with the existing
``Wallet`` model.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class MerchantListItem(BaseModel):
    """A single row in the paginated merchant directory (R8.1).

    Includes the identifier, display name, status, KYC completion flag, and
    creation date the Admin Panel's merchant directory renders.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    partner_id: str | None = None
    shop_id: str | None = None
    display_name: str
    legal_name: str
    status: str
    is_kyc_completed: bool
    created_at: datetime


class MerchantMemberOut(BaseModel):
    """A member of a merchant, projected for the detail view (R8.4)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    role: str
    email: str | None = None
    full_name: str | None = None
    joined_at: datetime


class WalletSummary(BaseModel):
    """Condensed wallet snapshot included in the merchant detail (R8.4).

    ``None`` is returned by the detail endpoint when a merchant has no wallet.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    balance: Decimal
    currency: str
    status: str
    low_balance_threshold: Decimal
    last_recharged_at: datetime | None = None


class MerchantDetail(BaseModel):
    """Detail record for a single merchant (R8.4).

    Carries the merchant's core attributes, its members, a wallet summary (or
    ``None``), and its status.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    partner_id: str | None = None
    shop_id: str | None = None
    slug: str
    display_name: str
    legal_name: str
    status: str
    is_kyc_completed: bool
    country: str
    address: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    range_km: float | None = None
    support_email: str | None = None
    support_phone: str | None = None
    created_at: datetime
    updated_at: datetime
    members: list[MerchantMemberOut]
    wallet: WalletSummary | None = None


class MerchantStatusResponse(BaseModel):
    """Result of a merchant suspend/activate transition (R8.5, R8.6)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    display_name: str
    status: str

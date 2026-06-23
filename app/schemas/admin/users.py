"""Response schemas for the consumer user directory (Requirement 7).

These back the ``users.py`` admin router. The listing and attribution endpoints
return the shared :class:`app.schemas.admin.listing.ListingEnvelope` parameterised
with the concrete item models below, so the Admin Panel consumes them with the
same pagination contract as every other directory.

Referral *attribution* (R7.9) is exposed as a nested :class:`UserAttribution`
object: the raw ``referred_by_code`` the user signed up with (the *referral
source*) plus the resolved *acquisition* — when that code belongs to a merchant,
the merchant's id and display name are surfaced; otherwise the source is treated
as organic.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr

#: Acquisition source classifications surfaced in :class:`UserAttribution`.
ACQUISITION_MERCHANT = "merchant"
ACQUISITION_ORGANIC = "organic"


class UserAttribution(BaseModel):
    """Referral source & acquisition attribution for a user (R7.9).

    Attributes:
        referred_by_code: The referral code the user signed up with, or ``None``
            when the user arrived without one.
        acquisition_source: ``"merchant"`` when ``referred_by_code`` resolves to
            a merchant's referral code, otherwise ``"organic"``.
        referring_merchant_id: The owning merchant's id when the source is a
            merchant, else ``None``.
        referring_merchant_name: The owning merchant's display name when the
            source is a merchant, else ``None``.
    """

    model_config = ConfigDict(from_attributes=True)

    referred_by_code: str | None = None
    acquisition_source: str = ACQUISITION_ORGANIC
    referring_merchant_id: uuid.UUID | None = None
    referring_merchant_name: str | None = None


class UserListItem(BaseModel):
    """A single row in the user directory listing (R7.1, R7.9)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    full_name: str | None = None
    email: EmailStr
    is_active: bool
    created_at: datetime
    attribution: UserAttribution


class UserDetail(BaseModel):
    """Detail record for a single user (R7.5).

    Includes the design profile, credit balance, and referral attribution in
    addition to the directory fields.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    full_name: str | None = None
    email: EmailStr
    is_active: bool
    created_at: datetime
    credit_balance: float
    design_profile: dict
    attribution: UserAttribution


class UserAttributionItem(BaseModel):
    """A single row in the attribution listing (R7.9)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    full_name: str | None = None
    email: EmailStr
    attribution: UserAttribution


class UserStatusResponse(BaseModel):
    """Result of a suspend/reactivate transition on a user (R7.7, R7.8)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    is_active: bool

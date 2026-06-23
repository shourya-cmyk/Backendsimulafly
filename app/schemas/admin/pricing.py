"""Request/response schemas for the admin Pricing Controls (Requirement 13).

Pricing controls wrap the existing :class:`app.models.wallet.PricingRule`
entity directly. The admin surface exposes the *current* rules (event type,
rate, rate type, currency, and effective window — R13.1), a create action
(R13.2), and an update action that opens a *new* effective window rather than
mutating history (R13.3).

Validation (R13.4, R13.5) is enforced both at the schema boundary (a negative
rate or an ``effective_until`` earlier than ``effective_from`` is rejected with
HTTP 422) and re-checked in the service for windows whose ``effective_from`` is
resolved server-side.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.wallet import RateType


class PricingRuleItem(BaseModel):
    """A single pricing rule row (R13.1).

    Carries the rule ``id``, the ``event_type`` it prices, the optional owning
    ``merchant_id`` (``None`` for a global default), the ``rate`` and
    ``rate_type``, the ``currency``, and the effective window
    (``effective_from`` / ``effective_until``).
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    event_type: str
    merchant_id: uuid.UUID | None = None
    rate: Decimal
    rate_type: str
    currency: str
    effective_from: datetime
    effective_until: datetime | None = None
    notes: str | None = None
    created_at: datetime
    updated_at: datetime


class PricingRuleListResponse(BaseModel):
    """Envelope for ``GET /admin/pricing-rules`` (R13.1)."""

    items: list[PricingRuleItem]


class PricingRuleCreate(BaseModel):
    """Payload for ``POST /admin/pricing-rules`` (R13.2).

    ``rate`` must be non-negative (R13.4) and, when both bounds are supplied,
    ``effective_until`` must not precede ``effective_from`` (R13.5); either
    violation is rejected with HTTP 422. ``effective_from`` defaults to the
    current time (resolved server-side) when omitted.
    """

    event_type: str = Field(min_length=1, max_length=32)
    merchant_id: uuid.UUID | None = None
    rate: Decimal = Field(ge=0)
    rate_type: RateType = RateType.FIXED
    currency: str = Field(default="INR", min_length=3, max_length=3)
    effective_from: datetime | None = None
    effective_until: datetime | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _check_window(self) -> "PricingRuleCreate":
        if (
            self.effective_from is not None
            and self.effective_until is not None
            and self.effective_until < self.effective_from
        ):
            raise ValueError("effective_until must not precede effective_from")
        return self


class PricingRuleUpdate(BaseModel):
    """Payload for ``PATCH /admin/pricing-rules/{id}`` (R13.3).

    The update opens a *new* effective window: the existing rule is closed and a
    new rule (inheriting ``event_type`` and ``merchant_id`` from the original)
    is inserted with the supplied overrides. Any omitted field inherits the
    original rule's value. ``effective_from`` is the moment the new window opens
    (defaulting to the current time); the original rule's ``effective_until`` is
    set to that moment so the windows do not overlap.

    A negative ``rate`` (R13.4) or an ``effective_until`` earlier than the new
    window's ``effective_from`` (R13.5) is rejected with HTTP 422.
    """

    rate: Decimal | None = Field(default=None, ge=0)
    rate_type: RateType | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    effective_from: datetime | None = None
    effective_until: datetime | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _check_window(self) -> "PricingRuleUpdate":
        if (
            self.effective_from is not None
            and self.effective_until is not None
            and self.effective_until < self.effective_from
        ):
            raise ValueError("effective_until must not precede effective_from")
        return self

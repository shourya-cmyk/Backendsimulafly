"""Pricing controls service (R13 — pricing rules).

``PricingService`` backs the admin ``pricing`` router. It wraps the existing
:class:`app.models.wallet.PricingRule` entity directly (the consumer-side
:func:`app.services.pricing.resolve_rate` rate-resolution logic is left
untouched) and owns three operations:

  * **List** the current pricing rules — by default only the rules whose
    effective window covers "now"; with ``include_history`` the full set of
    rules (including closed/superseded windows) is returned (R13.1).
  * **Create** a new rule after validating its rate and window (R13.2). A
    negative rate or an ``effective_until`` earlier than ``effective_from`` is
    rejected with HTTP 422 (R13.4, R13.5).
  * **Update** a rule by opening a *new* effective window (R13.3): the existing
    rule's ``effective_until`` is closed at the new window's ``effective_from``
    and a new rule (inheriting ``event_type``/``merchant_id``) is inserted with
    the supplied overrides — history is never mutated in place.

The router handles permission gating, auditing, and schema mapping; this
service owns persistence and the validation invariants.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.wallet import PricingRule
from app.schemas.admin.pricing import PricingRuleCreate, PricingRuleUpdate


def _validate_rate_and_window(
    rate: Decimal,
    effective_from: datetime,
    effective_until: datetime | None,
) -> None:
    """Guard the pricing invariants, raising HTTP 422 on violation.

    Mirrors the schema-level checks so windows whose ``effective_from`` is
    resolved server-side are still validated: a negative rate (R13.4) or an
    ``effective_until`` earlier than ``effective_from`` (R13.5) is rejected and
    nothing is persisted.
    """
    if rate < 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="rate must not be negative",
        )
    if effective_until is not None and effective_until < effective_from:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="effective_until must not precede effective_from",
        )


class PricingService:
    """List / create / update operations for pricing rules (R13)."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_rules(self, *, include_history: bool = False) -> list[PricingRule]:
        """Return pricing rules ordered by event type then newest window first.

        By default only the *current* rules are returned — those whose window
        covers the present moment (``effective_from <= now`` and
        ``effective_until`` is null or in the future). Passing
        ``include_history=True`` returns every rule, including superseded
        windows (R13.1).
        """
        stmt = select(PricingRule)
        if not include_history:
            now = datetime.now(timezone.utc)
            stmt = stmt.where(
                PricingRule.effective_from <= now,
                or_(
                    PricingRule.effective_until.is_(None),
                    PricingRule.effective_until > now,
                ),
            )
        stmt = stmt.order_by(
            PricingRule.event_type.asc(),
            PricingRule.effective_from.desc(),
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def get_rule(self, rule_id: uuid.UUID) -> PricingRule:
        """Fetch a single pricing rule; raise HTTP 404 when it does not exist."""
        rule = await self.db.get(PricingRule, rule_id)
        if rule is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="pricing rule not found",
            )
        return rule

    async def create_rule(self, data: PricingRuleCreate) -> PricingRule:
        """Persist a new pricing rule after validating it (R13.2).

        ``effective_from`` defaults to the current time when omitted. A negative
        rate or an inverted window is rejected with HTTP 422 and nothing is
        persisted (R13.4, R13.5).
        """
        effective_from = data.effective_from or datetime.now(timezone.utc)
        _validate_rate_and_window(data.rate, effective_from, data.effective_until)

        rule = PricingRule(
            event_type=data.event_type,
            merchant_id=data.merchant_id,
            rate=data.rate,
            rate_type=data.rate_type.value,
            currency=data.currency,
            effective_from=effective_from,
            effective_until=data.effective_until,
            notes=data.notes,
        )
        self.db.add(rule)
        await self.db.commit()
        await self.db.refresh(rule)
        return rule

    async def update_rule(
        self,
        rule_id: uuid.UUID,
        data: PricingRuleUpdate,
    ) -> PricingRule:
        """Open a new effective window for an existing rule (R13.3).

        The original rule is closed (its ``effective_until`` set to the new
        window's ``effective_from``) and a new rule is inserted inheriting the
        original ``event_type`` and ``merchant_id`` with the supplied overrides.
        History is never mutated in place. Returns the newly created rule.

        A missing rule raises HTTP 404; a negative rate or inverted window
        raises HTTP 422 and persists nothing (R13.4, R13.5).
        """
        existing = await self.get_rule(rule_id)

        new_from = data.effective_from or datetime.now(timezone.utc)
        new_rate = data.rate if data.rate is not None else existing.rate
        new_rate_type = (
            data.rate_type.value if data.rate_type is not None else existing.rate_type
        )
        new_currency = data.currency if data.currency is not None else existing.currency
        new_notes = data.notes if data.notes is not None else existing.notes

        _validate_rate_and_window(new_rate, new_from, data.effective_until)

        # Close the old window so the two rules do not overlap; never edit the
        # historical rate/type/window of the superseded rule.
        existing.effective_until = new_from

        new_rule = PricingRule(
            event_type=existing.event_type,
            merchant_id=existing.merchant_id,
            rate=new_rate,
            rate_type=new_rate_type,
            currency=new_currency,
            effective_from=new_from,
            effective_until=data.effective_until,
            notes=new_notes,
        )
        self.db.add(new_rule)
        await self.db.commit()
        await self.db.refresh(new_rule)
        return new_rule

"""Pricing rate resolution.

resolve_rate(db, event_type, merchant_id) returns (rate, rate_type) by checking:
  1. Per-merchant override active right now (highest priority)
  2. Global default active right now (merchant_id IS NULL)
  3. (0.0, "fixed") if no rule matches — caller can treat as "free"
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.wallet import PricingRule


async def resolve_rate(
    db: AsyncSession, event_type: str, merchant_id: uuid.UUID
) -> tuple[Decimal, str]:
    """Return (rate, rate_type) for this event_type + merchant.

    Returns (0.0, "fixed") if no matching rule exists.
    """
    now = datetime.now(timezone.utc)

    # First check for a per-merchant override
    stmt = (
        select(PricingRule)
        .where(
            PricingRule.event_type == event_type,
            PricingRule.merchant_id == merchant_id,
            PricingRule.effective_from <= now,
            or_(
                PricingRule.effective_until.is_(None),
                PricingRule.effective_until > now,
            ),
        )
        .order_by(PricingRule.effective_from.desc())
        .limit(1)
    )
    rule = (await db.execute(stmt)).scalar_one_or_none()
    if rule is not None:
        return rule.rate, rule.rate_type

    # Fall back to global default (merchant_id IS NULL)
    stmt = (
        select(PricingRule)
        .where(
            PricingRule.event_type == event_type,
            PricingRule.merchant_id.is_(None),
            PricingRule.effective_from <= now,
            or_(
                PricingRule.effective_until.is_(None),
                PricingRule.effective_until > now,
            ),
        )
        .order_by(PricingRule.effective_from.desc())
        .limit(1)
    )
    rule = (await db.execute(stmt)).scalar_one_or_none()
    if rule is not None:
        return rule.rate, rule.rate_type

    return Decimal("0"), "fixed"

"""Per-session dedup (spec §2.5 B1).

DB-backed: a UNIQUE constraint on (event_type, session_id, product_id, hour_bucket)
prevents double-billing within a 1-hour window. Redis swap is a future
optimization.

Dedup only applies to `click` and `ai_image_generation` per the spec.
Other event types are always billed.
"""
from __future__ import annotations

import time
import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event import BuyerEventDedup


_DEDUPABLE_EVENT_TYPES = {"click", "ai_image_generation"}


def is_dedupable(event_type: str) -> bool:
    return event_type in _DEDUPABLE_EVENT_TYPES


def _current_hour_bucket() -> int:
    """Epoch seconds // 3600 — collides if two events fall in the same hour."""
    return int(time.time()) // 3600


async def check_and_record(
    db: AsyncSession,
    *,
    event_type: str,
    session_id: str,
    product_id: uuid.UUID,
) -> bool:
    """Return True if this is the first occurrence (caller should bill).
    Return False if already seen within the current hour bucket.

    Non-dedupable event types always return True (caller decides what to do).
    """
    if not is_dedupable(event_type):
        return True

    row = BuyerEventDedup(
        event_type=event_type,
        user_session_id=session_id,
        merchant_product_id=product_id,
        hour_bucket=_current_hour_bucket(),
    )
    db.add(row)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        return False
    return True

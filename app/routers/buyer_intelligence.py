"""Buyer Intelligence endpoints for the merchant portal.

High-intent ShopperFeed:
  GET  /merchant/buyer-intelligence/       — paginated list of shoppers who
       interacted with this merchant's products, with computed intent score.
       Non-unlocked shoppers only show city.
  POST /merchant/buyer-intelligence/{user_id}/unlock — deduct ₹30, reveal contact.
  GET  /merchant/buyer-intelligence/unlocked          — already-unlocked contacts.

Intent score formula (per-user, per-merchant):
  click              ×1
  ai_rag_mention     ×2
  ai_image_generation ×5
  external_redirect  ×8
  simulafly_purchase ×15  (from lead conversions)
Score capped at 99.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.models.buyer_intelligence import MerchantBuyerAccess
from app.models.event import BuyerEvent
from app.models.lead import BuyerLead
from app.models.user import User
from app.models.wallet import Wallet
from app.utils.dependencies import DBSession
from app.utils.merchant_context import CurrentMerchantContext

router = APIRouter(prefix="/merchant/buyer-intelligence", tags=["buyer-intelligence"])

UNLOCK_COST = 30  # INR per buyer reveal

# Intent weights
_WEIGHTS: dict[str, int] = {
    "click": 1,
    "ai_rag_mention": 2,
    "ai_image_generation": 5,
    "external_redirect": 8,
    "simulafly_purchase": 15,
}


def _intent_score(events: list[tuple[str, int]]) -> int:
    raw = sum(_WEIGHTS.get(ev_type, 0) * count for ev_type, count in events)
    return min(raw, 99)


def _intent_label(score: int) -> tuple[str, str]:
    """Returns (label, tier) based on score."""
    if score >= 80:
        return "Purchase Ready", "ready"
    if score >= 55:
        return "High Intent", "high"
    if score >= 30:
        return "Medium Intent", "medium"
    return "Low Intent", "low"


class ShopperOut(BaseModel):
    user_id: str
    city: str
    # only populated when unlocked
    name: str | None = None
    phone: str | None = None
    email: str | None = None
    intent_score: int
    intent_label: str
    intent_tier: str
    interaction_count: int
    unlocked: bool
    # per-event-type counts
    click_count: int = 0
    rag_count: int = 0
    image_count: int = 0
    redirect_count: int = 0


class PaginatedShoppers(BaseModel):
    items: list[ShopperOut]
    total: int
    limit: int
    offset: int


class UnlockResponse(BaseModel):
    user_id: str
    name: str
    phone: str | None
    email: str | None
    city: str
    intent_score: int
    intent_label: str
    intent_tier: str
    interaction_count: int
    unlocked: bool = True
    click_count: int
    rag_count: int
    image_count: int
    redirect_count: int


async def _aggregate_shoppers(
    db: DBSession,
    merchant_id: uuid.UUID,
    since_days: int = 30,
) -> list[dict]:
    """Aggregate buyer events per user for this merchant."""
    since = datetime.now(timezone.utc) - timedelta(days=since_days)
    sql = text(
        """
        SELECT
            be.user_id,
            COUNT(*) FILTER (WHERE be.event_type = 'click') AS click_count,
            COUNT(*) FILTER (WHERE be.event_type = 'ai_rag_mention') AS rag_count,
            COUNT(*) FILTER (WHERE be.event_type = 'ai_image_generation') AS image_count,
            COUNT(*) FILTER (WHERE be.event_type = 'external_redirect') AS redirect_count,
            COUNT(*) AS total_interactions
        FROM buyer_events be
        WHERE be.merchant_id = :mid
          AND be.created_at >= :since
        GROUP BY be.user_id
        HAVING COUNT(*) >= 1
        ORDER BY (
            COUNT(*) FILTER (WHERE be.event_type = 'click') * 1 +
            COUNT(*) FILTER (WHERE be.event_type = 'ai_rag_mention') * 2 +
            COUNT(*) FILTER (WHERE be.event_type = 'ai_image_generation') * 5 +
            COUNT(*) FILTER (WHERE be.event_type = 'external_redirect') * 8
        ) DESC
        """
    )
    res = await db.execute(sql, {"mid": merchant_id, "since": since})
    return [dict(r._mapping) for r in res.fetchall()]


@router.get("/", response_model=PaginatedShoppers)
async def list_shoppers(
    db: DBSession,
    ctx: CurrentMerchantContext,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    since_days: int = Query(default=30, ge=1, le=90),
) -> dict:
    rows = await _aggregate_shoppers(db, ctx.merchant.id, since_days)
    total = len(rows)
    page = rows[offset: offset + limit]

    if not page:
        return {"items": [], "total": total, "limit": limit, "offset": offset}

    user_ids = [r["user_id"] for r in page]

    # Fetch user profiles
    users_res = await db.execute(select(User).where(User.id.in_(user_ids)))
    users_by_id = {u.id: u for u in users_res.scalars().all()}

    # Fetch which ones are already unlocked
    unlocked_res = await db.execute(
        select(MerchantBuyerAccess.user_id).where(
            MerchantBuyerAccess.merchant_id == ctx.merchant.id,
            MerchantBuyerAccess.user_id.in_(user_ids),
        )
    )
    unlocked_ids = {row[0] for row in unlocked_res.fetchall()}

    # Fetch latest lead per user (for city + phone)
    leads_res = await db.execute(
        select(BuyerLead)
        .where(
            BuyerLead.merchant_id == ctx.merchant.id,
            BuyerLead.user_id.in_(user_ids),
        )
        .order_by(BuyerLead.created_at.desc())
    )
    leads_by_user: dict[uuid.UUID, BuyerLead] = {}
    for lead in leads_res.scalars().all():
        leads_by_user.setdefault(lead.user_id, lead)

    items: list[ShopperOut] = []
    for r in page:
        uid = r["user_id"]
        user = users_by_id.get(uid)
        if not user:
            continue
        lead = leads_by_user.get(uid)
        events = [
            ("click", r["click_count"]),
            ("ai_rag_mention", r["rag_count"]),
            ("ai_image_generation", r["image_count"]),
            ("external_redirect", r["redirect_count"]),
        ]
        score = _intent_score(events)
        label, tier = _intent_label(score)
        is_unlocked = uid in unlocked_ids
        items.append(
            ShopperOut(
                user_id=str(uid),
                city=lead.delivery_city if lead else "India",
                name=user.full_name if is_unlocked else None,
                phone=lead.delivery_phone if (is_unlocked and lead) else None,
                email=user.email if is_unlocked else None,
                intent_score=score,
                intent_label=label,
                intent_tier=tier,
                interaction_count=int(r["total_interactions"]),
                unlocked=is_unlocked,
                click_count=int(r["click_count"]),
                rag_count=int(r["rag_count"]),
                image_count=int(r["image_count"]),
                redirect_count=int(r["redirect_count"]),
            )
        )

    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.post("/{user_id}/unlock", response_model=UnlockResponse)
async def unlock_shopper(
    user_id: uuid.UUID,
    db: DBSession,
    ctx: CurrentMerchantContext,
) -> UnlockResponse:
    # Check if already unlocked
    existing = await db.execute(
        select(MerchantBuyerAccess).where(
            MerchantBuyerAccess.merchant_id == ctx.merchant.id,
            MerchantBuyerAccess.user_id == user_id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="already unlocked")

    # Check wallet balance
    wallet_res = await db.execute(
        select(Wallet).where(Wallet.merchant_id == ctx.merchant.id)
    )
    wallet = wallet_res.scalar_one_or_none()
    if not wallet or float(wallet.balance) < UNLOCK_COST:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"insufficient wallet balance; need ₹{UNLOCK_COST}",
        )

    # Deduct from wallet
    from decimal import Decimal
    wallet.balance = wallet.balance - Decimal(str(UNLOCK_COST))

    # Record unlock
    access = MerchantBuyerAccess(
        merchant_id=ctx.merchant.id,
        user_id=user_id,
        unlock_cost=UNLOCK_COST,
    )
    db.add(access)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="already unlocked")

    # Fetch user
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")

    # Fetch latest lead for city + phone
    lead_res = await db.execute(
        select(BuyerLead)
        .where(BuyerLead.merchant_id == ctx.merchant.id, BuyerLead.user_id == user_id)
        .order_by(BuyerLead.created_at.desc())
        .limit(1)
    )
    lead = lead_res.scalar_one_or_none()

    # Re-aggregate events for this user
    since = datetime.now(timezone.utc) - timedelta(days=30)
    sql = text(
        """
        SELECT
            COUNT(*) FILTER (WHERE event_type = 'click') AS click_count,
            COUNT(*) FILTER (WHERE event_type = 'ai_rag_mention') AS rag_count,
            COUNT(*) FILTER (WHERE event_type = 'ai_image_generation') AS image_count,
            COUNT(*) FILTER (WHERE event_type = 'external_redirect') AS redirect_count,
            COUNT(*) AS total_interactions
        FROM buyer_events
        WHERE merchant_id = :mid AND user_id = :uid AND created_at >= :since
        """
    )
    ev_res = await db.execute(sql, {"mid": ctx.merchant.id, "uid": user_id, "since": since})
    ev = dict(ev_res.fetchone()._mapping)
    events = [
        ("click", ev["click_count"]),
        ("ai_rag_mention", ev["rag_count"]),
        ("ai_image_generation", ev["image_count"]),
        ("external_redirect", ev["redirect_count"]),
    ]
    score = _intent_score(events)
    label, tier = _intent_label(score)

    return UnlockResponse(
        user_id=str(user_id),
        name=user.full_name or user.email,
        phone=lead.delivery_phone if lead else None,
        email=user.email,
        city=lead.delivery_city if lead else "India",
        intent_score=score,
        intent_label=label,
        intent_tier=tier,
        interaction_count=int(ev["total_interactions"]),
        click_count=int(ev["click_count"]),
        rag_count=int(ev["rag_count"]),
        image_count=int(ev["image_count"]),
        redirect_count=int(ev["redirect_count"]),
    )

"""Merchant-facing analytics endpoints — aggregations over BuyerEvent + LedgerEntry."""
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from app.models.event import BuyerEvent, LedgerEntry
from app.models.merchant_product import MerchantProduct
from app.schemas.analytics import (
    AnalyticsSummary,
    DiagnosticsResponse,
    ProductAnalyticsDetail,
    ProductPerformanceList,
)
from app.utils.dependencies import DBSession
from app.utils.merchant_context import CurrentMerchantContext

router = APIRouter(prefix="/merchant/analytics", tags=["merchant-analytics"])


def _date_window(days: int = 30) -> tuple[datetime, datetime]:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    return start, end


@router.get("/summary", response_model=AnalyticsSummary)
async def analytics_summary(
    db: DBSession,
    ctx: CurrentMerchantContext,
    days: int = Query(default=30, ge=1, le=365),
) -> dict:
    start, end = _date_window(days)
    mid = ctx.merchant.id

    total_q = select(func.count()).select_from(MerchantProduct).where(
        MerchantProduct.merchant_id == mid
    )
    pub_q = select(func.count()).select_from(MerchantProduct).where(
        MerchantProduct.merchant_id == mid,
        MerchantProduct.status == "published",
    )
    total = (await db.execute(total_q)).scalar_one()
    published = (await db.execute(pub_q)).scalar_one()

    event_stmt = (
        select(BuyerEvent.event_type, func.count())
        .where(
            BuyerEvent.merchant_id == mid,
            BuyerEvent.created_at.between(start, end),
        )
        .group_by(BuyerEvent.event_type)
    )
    counts = {row[0]: row[1] for row in (await db.execute(event_stmt)).all()}

    spend_stmt = select(func.coalesce(func.sum(LedgerEntry.amount), 0)).where(
        LedgerEntry.merchant_id == mid,
        LedgerEntry.entry_type == "deduction",
        LedgerEntry.created_at.between(start, end),
    )
    spend_signed = float((await db.execute(spend_stmt)).scalar_one())
    total_spend = -spend_signed

    impressions = counts.get("impression", 0)
    clicks = counts.get("click", 0)
    ctr = clicks / impressions if impressions else 0.0

    return {
        "total_products": total,
        "published_products": published,
        "impressions": impressions,
        "clicks": clicks,
        "ai_mentions": counts.get("ai_rag_mention", 0),
        "ai_image_generations": counts.get("ai_image_generation", 0),
        "external_redirects": counts.get("external_redirect", 0),
        "total_spend": total_spend,
        "ctr": ctr,
        "start_date": start,
        "end_date": end,
    }


@router.get("/products", response_model=ProductPerformanceList)
async def analytics_products(
    db: DBSession,
    ctx: CurrentMerchantContext,
    days: int = Query(default=30, ge=1, le=365),
) -> dict:
    start, end = _date_window(days)
    mid = ctx.merchant.id

    stmt = (
        select(
            BuyerEvent.merchant_product_id,
            BuyerEvent.event_type,
            func.count().label("n"),
        )
        .where(
            BuyerEvent.merchant_id == mid,
            BuyerEvent.created_at.between(start, end),
            BuyerEvent.merchant_product_id.is_not(None),
        )
        .group_by(BuyerEvent.merchant_product_id, BuyerEvent.event_type)
    )
    counts_by_product: dict = {}
    for row in (await db.execute(stmt)).all():
        pid, et, n = row
        counts_by_product.setdefault(pid, {})[et] = n

    spend_stmt = (
        select(BuyerEvent.merchant_product_id, func.sum(LedgerEntry.amount).label("amt"))
        .join(LedgerEntry, LedgerEntry.related_event_id == BuyerEvent.id)
        .where(
            BuyerEvent.merchant_id == mid,
            BuyerEvent.created_at.between(start, end),
            BuyerEvent.merchant_product_id.is_not(None),
        )
        .group_by(BuyerEvent.merchant_product_id)
    )
    spend_by_product = {row[0]: -float(row[1] or 0) for row in (await db.execute(spend_stmt)).all()}

    product_stmt = select(MerchantProduct).where(MerchantProduct.merchant_id == mid)
    products = {p.id: p for p in (await db.execute(product_stmt)).scalars().all()}

    items = []
    for pid, p in products.items():
        c = counts_by_product.get(pid, {})
        imps = c.get("impression", 0)
        clicks = c.get("click", 0)
        items.append(
            {
                "product_id": p.id,
                "title": p.title,
                "sku": p.sku,
                "status": p.status,
                "impressions": imps,
                "clicks": clicks,
                "ai_mentions": c.get("ai_rag_mention", 0),
                "ai_image_generations": c.get("ai_image_generation", 0),
                "external_redirects": c.get("external_redirect", 0),
                "spend": spend_by_product.get(pid, 0.0),
                "ctr": (clicks / imps) if imps else 0.0,
                "health_score": p.health_score,
            }
        )
    items.sort(key=lambda x: x["spend"], reverse=True)
    return {"items": items, "start_date": start, "end_date": end}


@router.get("/products/{product_id}", response_model=ProductAnalyticsDetail)
async def analytics_product_detail(
    product_id: uuid.UUID,
    db: DBSession,
    ctx: CurrentMerchantContext,
):
    product = await db.get(MerchantProduct, product_id)
    if not product or product.merchant_id != ctx.merchant.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="product not found")

    start, end = _date_window(30)

    stmt = (
        select(BuyerEvent.event_type, func.count())
        .where(
            BuyerEvent.merchant_product_id == product_id,
            BuyerEvent.created_at.between(start, end),
        )
        .group_by(BuyerEvent.event_type)
    )
    counts = {row[0]: row[1] for row in (await db.execute(stmt)).all()}

    spend_stmt = (
        select(func.sum(LedgerEntry.amount))
        .join(BuyerEvent, BuyerEvent.id == LedgerEntry.related_event_id)
        .where(
            BuyerEvent.merchant_product_id == product_id,
            BuyerEvent.created_at.between(start, end),
        )
    )
    spend_signed = float((await db.execute(spend_stmt)).scalar() or 0)
    spend = -spend_signed

    # Top RAG queries (Postgres JSONB ->> required; fallback to empty for SQLite)
    top_rag: list[dict] = []
    try:
        rag_stmt = (
            select(
                BuyerEvent.context["prompt"].astext.label("prompt"),
                func.count().label("n"),
            )
            .where(
                BuyerEvent.merchant_product_id == product_id,
                BuyerEvent.event_type == "ai_rag_mention",
                BuyerEvent.created_at.between(start, end),
            )
            .group_by("prompt")
            .order_by(func.count().desc())
            .limit(10)
        )
        rag_rows = (await db.execute(rag_stmt)).all()
        top_rag = [{"query": (q or "")[:200], "count": n} for q, n in rag_rows]
    except Exception:
        pass

    # Daily impressions + clicks for last 7 days
    daily_imps = [0] * 7
    daily_clicks = [0] * 7
    for et, daily_arr in (("impression", daily_imps), ("click", daily_clicks)):
        try:
            d_stmt = (
                select(
                    func.date_trunc("day", BuyerEvent.created_at).label("day"),
                    func.count(),
                )
                .where(
                    BuyerEvent.merchant_product_id == product_id,
                    BuyerEvent.event_type == et,
                    BuyerEvent.created_at >= end - timedelta(days=7),
                )
                .group_by("day")
            )
            for day, n in (await db.execute(d_stmt)).all():
                idx = (end.date() - day.date()).days
                if 0 <= idx < 7:
                    daily_arr[6 - idx] = n
        except Exception:
            pass

    impressions = counts.get("impression", 0)
    clicks = counts.get("click", 0)
    return {
        "product_id": product.id,
        "title": product.title,
        "sku": product.sku,
        "status": product.status,
        "impressions": impressions,
        "clicks": clicks,
        "ai_mentions": counts.get("ai_rag_mention", 0),
        "ai_image_generations": counts.get("ai_image_generation", 0),
        "external_redirects": counts.get("external_redirect", 0),
        "spend": spend,
        "ctr": (clicks / impressions) if impressions else 0.0,
        "health_score": product.health_score,
        "health_reason": product.health_reason,
        "ai_relevance_score": float(product.ai_relevance_score) if product.ai_relevance_score else None,
        "top_rag_queries": top_rag,
        "daily_impressions": daily_imps,
        "daily_clicks": daily_clicks,
    }


@router.get("/diagnostics", response_model=DiagnosticsResponse)
async def analytics_diagnostics(
    db: DBSession, ctx: CurrentMerchantContext
) -> dict:
    """Flag products with quality issues."""
    mid = ctx.merchant.id
    start, _ = _date_window(30)

    stmt = (
        select(
            BuyerEvent.merchant_product_id,
            BuyerEvent.event_type,
            func.count().label("n"),
        )
        .where(
            BuyerEvent.merchant_id == mid,
            BuyerEvent.created_at >= start,
            BuyerEvent.merchant_product_id.is_not(None),
        )
        .group_by(BuyerEvent.merchant_product_id, BuyerEvent.event_type)
    )
    counts_by_product: dict = {}
    for row in (await db.execute(stmt)).all():
        pid, et, n = row
        counts_by_product.setdefault(pid, {})[et] = n

    product_stmt = select(MerchantProduct).where(MerchantProduct.merchant_id == mid)
    products = {p.id: p for p in (await db.execute(product_stmt)).scalars().all()}

    alerts: list[dict] = []
    for pid, p in products.items():
        c = counts_by_product.get(pid, {})
        imps = c.get("impression", 0)
        clicks = c.get("click", 0)

        if imps >= 100 and clicks == 0:
            alerts.append(
                {
                    "product_id": p.id,
                    "title": p.title,
                    "issue_type": "zero_click",
                    "detail": f"{imps} impressions, 0 clicks — review thumbnail or price.",
                }
            )

        if p.ai_relevance_score is not None and float(p.ai_relevance_score) < 50:
            alerts.append(
                {
                    "product_id": p.id,
                    "title": p.title,
                    "issue_type": "low_ai_relevance",
                    "detail": f"AI relevance score {float(p.ai_relevance_score):.0f}/100 — add more descriptive metadata.",
                }
            )

        if not p.description or len(p.description) < 30:
            alerts.append(
                {
                    "product_id": p.id,
                    "title": p.title,
                    "issue_type": "missing_metadata",
                    "detail": "Description is missing or too short (< 30 chars) — affects AI ranking.",
                }
            )

    return {"alerts": alerts}

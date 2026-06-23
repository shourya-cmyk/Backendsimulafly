"""Merchant-facing analytics endpoints — aggregations over BuyerEvent + LedgerEntry."""
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from app.models.event import BuyerEvent, LedgerEntry
from app.models.merchant_product import MerchantProduct
from app.models.lead import BuyerLead, Order, OrderStatus
from app.schemas.analytics import (
    AnalyticsSummary,
    DiagnosticsResponse,
    ProductAnalyticsDetail,
    ProductPerformanceList,
)
from app.utils.dependencies import DBSession
from app.utils.merchant_context import CurrentMerchantContext
from app.core.config import get_settings

router = APIRouter(prefix="/merchant/analytics", tags=["merchant-analytics"])


def _parse_day(day_val):
    if not day_val:
        return None
    if isinstance(day_val, str):
        try:
            return datetime.strptime(day_val.split()[0], "%Y-%m-%d").date()
        except Exception:
            return None
    if hasattr(day_val, "date"):
        return day_val.date()
    return day_val


def _date_window(days: int = 30) -> tuple[datetime, datetime]:
    end = datetime.now(timezone.utc).replace(tzinfo=None)
    start = end - timedelta(days=days)
    return start, end


@router.get("/summary", response_model=AnalyticsSummary)
async def analytics_summary(
    db: DBSession,
    ctx: CurrentMerchantContext,
    days: int = Query(default=30, ge=1, le=365),
) -> dict:
    from app.models.cart import CartItem
    from app.models.lead import Order, OrderStatus
    from sqlalchemy import or_, and_

    start, end = _date_window(days)
    mid = ctx.merchant.id

    total_q = select(func.count()).select_from(MerchantProduct).where(
        MerchantProduct.merchant_id == mid
    )
    pub_q = select(func.count()).select_from(MerchantProduct).where(
        MerchantProduct.merchant_id == mid,
        MerchantProduct.status == "published",
    )
    archived_q = select(func.count()).select_from(MerchantProduct).where(
        MerchantProduct.merchant_id == mid,
        MerchantProduct.status == "archived",
    )
    draft_q = select(func.count()).select_from(MerchantProduct).where(
        MerchantProduct.merchant_id == mid,
        MerchantProduct.status == "draft",
    )
    paused_q = select(func.count()).select_from(MerchantProduct).where(
        MerchantProduct.merchant_id == mid,
        MerchantProduct.status == "paused_insufficient_funds",
    )

    total = (await db.execute(total_q)).scalar_one()
    published = (await db.execute(pub_q)).scalar_one()
    archived = (await db.execute(archived_q)).scalar_one()
    draft = (await db.execute(draft_q)).scalar_one()
    paused = (await db.execute(paused_q)).scalar_one()

    # Calculate leads and pipeline value from CartItem table
    cart_items_stmt = (
        select(CartItem.quantity, MerchantProduct.in_app_price)
        .join(MerchantProduct, MerchantProduct.id == CartItem.merchant_product_id)
        .where(MerchantProduct.merchant_id == mid)
    )
    cart_items = (await db.execute(cart_items_stmt)).all()
    pipeline_value = sum(item.quantity * float(item.in_app_price or 0) for item in cart_items)

    leads_user_ids_stmt = (
        select(func.count(func.distinct(CartItem.user_id)))
        .join(MerchantProduct, MerchantProduct.id == CartItem.merchant_product_id)
        .where(MerchantProduct.merchant_id == mid)
    )
    total_leads = (await db.execute(leads_user_ids_stmt)).scalar_one()

    # Calculate drop rate
    total_orders_stmt = select(func.count(Order.id)).where(Order.merchant_id == mid)
    total_orders = (await db.execute(total_orders_stmt)).scalar_one()

    drop_stmt = select(func.count(Order.id)).where(
        Order.merchant_id == mid,
        or_(
            Order.status == OrderStatus.CANCELLED.value,
            and_(
                Order.status == OrderStatus.PENDING_MERCHANT_CONTACT.value,
                Order.created_at < datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=12)
            )
        )
    )
    drop_instances = (await db.execute(drop_stmt)).scalar_one()
    drop_rate = drop_instances / total_orders if total_orders > 0 else 0.0

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

    # Calculate daily metrics
    days_list = []
    curr = start.date()
    while curr <= end.date():
        days_list.append(curr)
        curr += timedelta(days=1)

    daily_data = {
        d: {
            "spend": 0.0,
            "revenue": 0.0,
            "pipeline": 0.0,
            "drop_rate": 0.0,
            "impressions": 0,
            "clicks": 0,
            "interactions": 0,
            "leads": 0,
            "converted": 0,
            "lost": 0,
        }
        for d in days_list
    }

    # Fetch daily spend
    daily_spend_stmt = select(LedgerEntry.created_at, LedgerEntry.amount).where(
        LedgerEntry.merchant_id == mid,
        LedgerEntry.entry_type == "deduction",
        LedgerEntry.created_at.between(start, end),
    )
    ledger_rows = (await db.execute(daily_spend_stmt)).all()
    for created_at, amount in ledger_rows:
        day_date = created_at.date()
        if day_date in daily_data:
            daily_data[day_date]["spend"] += float(-amount)

    # Fetch daily revenue from converted leads
    from app.models.lead import BuyerLead
    daily_rev_stmt = select(BuyerLead.converted_at, BuyerLead.estimated_value).where(
        BuyerLead.merchant_id == mid,
        BuyerLead.status == "converted",
        BuyerLead.converted_at.between(start, end),
    )
    lead_rows = (await db.execute(daily_rev_stmt)).all()
    for converted_at, est_val in lead_rows:
        if converted_at:
            day_date = converted_at.date()
            if day_date in daily_data:
                daily_data[day_date]["revenue"] += float(est_val)

    # Fetch daily buyer events
    daily_events_stmt = select(BuyerEvent.created_at, BuyerEvent.event_type).where(
        BuyerEvent.merchant_id == mid,
        BuyerEvent.created_at.between(start, end),
    )
    event_rows = (await db.execute(daily_events_stmt)).all()
    for created_at, event_type in event_rows:
        day_date = created_at.date()
        if day_date in daily_data:
            if event_type == "impression":
                daily_data[day_date]["impressions"] += 1
            elif event_type == "click":
                daily_data[day_date]["clicks"] += 1
            elif event_type in ("ai_rag_mention", "ai_image_generation"):
                daily_data[day_date]["interactions"] += 1

    # Fetch daily leads raised
    daily_leads_stmt = select(BuyerLead.created_at).where(
        BuyerLead.merchant_id == mid,
        BuyerLead.created_at.between(start, end),
    )
    lead_rows_daily = (await db.execute(daily_leads_stmt)).all()
    for (created_at,) in lead_rows_daily:
        day_date = created_at.date()
        if day_date in daily_data:
            daily_data[day_date]["leads"] += 1

    # Fetch daily leads converted
    daily_conv_stmt = select(BuyerLead.converted_at).where(
        BuyerLead.merchant_id == mid,
        BuyerLead.status == "converted",
        BuyerLead.converted_at.between(start, end),
    )
    conv_rows_daily = (await db.execute(daily_conv_stmt)).all()
    for (converted_at,) in conv_rows_daily:
        if converted_at:
            day_date = converted_at.date()
            if day_date in daily_data:
                daily_data[day_date]["converted"] += 1

    # Fetch daily leads lost
    daily_lost_stmt = select(BuyerLead.updated_at).where(
        BuyerLead.merchant_id == mid,
        BuyerLead.status == "lost",
        BuyerLead.updated_at.between(start, end),
    )
    lost_rows_daily = (await db.execute(daily_lost_stmt)).all()
    for (updated_at,) in lost_rows_daily:
        if updated_at:
            day_date = updated_at.date()
            if day_date in daily_data:
                daily_data[day_date]["lost"] += 1

    # Fetch daily pipeline created (cart additions)
    is_sqlite = "sqlite" in get_settings().DATABASE_URL.lower()
    if is_sqlite:
        daily_pipe_stmt = (
            select(func.strftime("%Y-%m-%d", CartItem.added_at).label("day"), func.sum(CartItem.quantity * MerchantProduct.in_app_price))
            .join(MerchantProduct, MerchantProduct.id == CartItem.merchant_product_id)
            .where(MerchantProduct.merchant_id == mid, CartItem.added_at.between(start, end))
            .group_by("day")
        )
    else:
        daily_pipe_stmt = (
            select(func.date_trunc("day", CartItem.added_at).label("day"), func.sum(CartItem.quantity * MerchantProduct.in_app_price))
            .join(MerchantProduct, MerchantProduct.id == CartItem.merchant_product_id)
            .where(MerchantProduct.merchant_id == mid, CartItem.added_at.between(start, end))
            .group_by("day")
        )
    pipe_rows = (await db.execute(daily_pipe_stmt)).all()
    daily_pipe = {_parse_day(row.day): float(row[1] or 0) for row in pipe_rows if row.day}
    for day_date, val in daily_pipe.items():
        if day_date in daily_data:
            daily_data[day_date]["pipeline"] = val

    # Fetch daily drop rate from orders created on that day
    if is_sqlite:
        daily_orders_stmt = (
            select(func.strftime("%Y-%m-%d", Order.created_at).label("day"), func.count(Order.id))
            .where(Order.merchant_id == mid, Order.created_at.between(start, end))
            .group_by("day")
        )
    else:
        daily_orders_stmt = (
            select(func.date_trunc("day", Order.created_at).label("day"), func.count(Order.id))
            .where(Order.merchant_id == mid, Order.created_at.between(start, end))
            .group_by("day")
        )
    orders_rows = (await db.execute(daily_orders_stmt)).all()
    daily_orders = {_parse_day(row.day): row[1] for row in orders_rows if row.day}

    if is_sqlite:
        daily_drops_stmt = (
            select(func.strftime("%Y-%m-%d", Order.created_at).label("day"), func.count(Order.id))
            .where(
                Order.merchant_id == mid,
                Order.created_at.between(start, end),
                or_(
                    Order.status == OrderStatus.CANCELLED.value,
                    and_(
                        Order.status == OrderStatus.PENDING_MERCHANT_CONTACT.value,
                        Order.created_at < datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=12)
                    )
                )
            )
            .group_by("day")
        )
    else:
        daily_drops_stmt = (
            select(func.date_trunc("day", Order.created_at).label("day"), func.count(Order.id))
            .where(
                Order.merchant_id == mid,
                Order.created_at.between(start, end),
                or_(
                    Order.status == OrderStatus.CANCELLED.value,
                    and_(
                        Order.status == OrderStatus.PENDING_MERCHANT_CONTACT.value,
                        Order.created_at < datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=12)
                    )
                )
            )
            .group_by("day")
        )
    drops_rows = (await db.execute(daily_drops_stmt)).all()
    daily_drops = {_parse_day(row.day): row[1] for row in drops_rows if row.day}

    for day_date, total_ord in daily_orders.items():
        if day_date in daily_data:
            cancelled_count = daily_drops.get(day_date, 0)
            daily_data[day_date]["drop_rate"] = (cancelled_count / total_ord) if total_ord > 0 else 0.0

    daily_metrics = [
        {
            "date": d.strftime("%Y-%m-%d"),
            "spend": daily_data[d]["spend"],
            "revenue": daily_data[d]["revenue"],
            "pipeline": daily_data[d]["pipeline"],
            "drop_rate": daily_data[d]["drop_rate"],
            "impressions": daily_data[d]["impressions"],
            "clicks": daily_data[d]["clicks"],
            "interactions": daily_data[d]["interactions"],
            "leads": daily_data[d]["leads"],
            "converted": daily_data[d]["converted"],
            "lost": daily_data[d]["lost"],
        }
        for d in days_list
    ]

    # Additional calculations
    converted_leads_stmt = select(func.count(BuyerLead.id)).where(
        BuyerLead.merchant_id == mid,
        BuyerLead.status == "converted",
        BuyerLead.converted_at.between(start, end)
    )
    converted_leads = (await db.execute(converted_leads_stmt)).scalar_one()

    pending_leads_stmt = select(func.count(Order.id)).where(
        Order.merchant_id == mid,
        Order.status == OrderStatus.PENDING_MERCHANT_CONTACT.value
    )
    pending_leads_count = (await db.execute(pending_leads_stmt)).scalar_one()

    reach_stmt = select(func.count(func.distinct(BuyerEvent.user_id))).where(
        BuyerEvent.merchant_id == mid,
        BuyerEvent.created_at.between(start, end)
    )
    reach_count = (await db.execute(reach_stmt)).scalar_one()

    # Top queries calculation
    top_queries = []
    try:
        if is_sqlite:
            prompt_expr = func.json_extract(BuyerEvent.context, '$.prompt')
        else:
            prompt_expr = BuyerEvent.context["prompt"].astext

        rag_summary_stmt = (
            select(
                prompt_expr.label("prompt"),
                MerchantProduct.id.label("product_id"),
                MerchantProduct.title.label("product_title"),
                func.count(BuyerEvent.id).label("count"),
            )
            .join(MerchantProduct, MerchantProduct.id == BuyerEvent.merchant_product_id)
            .where(
                BuyerEvent.merchant_id == mid,
                BuyerEvent.event_type == "ai_rag_mention",
                BuyerEvent.created_at.between(start, end),
            )
            .group_by("prompt", MerchantProduct.id, MerchantProduct.title)
            .order_by(func.count(BuyerEvent.id).desc())
            .limit(10)
        )
        rag_summary_rows = (await db.execute(rag_summary_stmt)).all()

        for r in rag_summary_rows:
            q_prompt = r.prompt
            pid = r.product_id
            p_title = r.product_title
            cnt = r.count

            user_stmt = select(func.distinct(BuyerEvent.user_id)).where(
                BuyerEvent.merchant_product_id == pid,
                BuyerEvent.event_type == "ai_rag_mention",
                prompt_expr == q_prompt,
            )
            users_res = await db.execute(user_stmt)
            user_ids = [row[0] for row in users_res.all()]

            if not user_ids:
                top_queries.append({
                    "query": q_prompt,
                    "product_title": p_title,
                    "count": cnt,
                    "conversion_rate": 0.0
                })
                continue

            if is_sqlite:
                lead_filter = BuyerLead.product_ids.like(f'%"{pid}"%')
            else:
                lead_filter = BuyerLead.product_ids.contains([str(pid)])

            converted_stmt = select(func.count(func.distinct(BuyerLead.user_id))).where(
                BuyerLead.user_id.in_(user_ids),
                BuyerLead.merchant_id == mid,
                BuyerLead.status == "converted",
                lead_filter,
            )
            conv_users = (await db.execute(converted_stmt)).scalar_one()

            conversion_rate = (conv_users / len(user_ids)) if user_ids else 0.0
            top_queries.append({
                "query": q_prompt,
                "product_title": p_title,
                "count": cnt,
                "conversion_rate": conversion_rate
            })
    except Exception as e:
        print(f"Error calculating top queries: {e}")

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
        "daily_metrics": daily_metrics,
        "total_leads": total_leads,
        "pipeline_value": pipeline_value,
        "drop_rate": drop_rate,
        "catalog_published": published,
        "catalog_archived": archived,
        "catalog_draft": draft,
        "catalog_paused": paused,
        "top_queries": top_queries,
        "converted_leads": converted_leads,
        "pending_leads_count": pending_leads_count,
        "reach_count": reach_count,
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

    is_sqlite = "sqlite" in get_settings().DATABASE_URL.lower()

    # Query 7-day daily impressions for all products in a single grouped query
    daily_impressions_by_product = {p_id: [0] * 7 for p_id in products.keys()}
    try:
        if is_sqlite:
            d_stmt = (
                select(
                    BuyerEvent.merchant_product_id,
                    func.strftime("%Y-%m-%d", BuyerEvent.created_at).label("day"),
                    func.count().label("n"),
                )
                .where(
                    BuyerEvent.merchant_id == mid,
                    BuyerEvent.event_type == "impression",
                    BuyerEvent.created_at >= end - timedelta(days=7),
                    BuyerEvent.merchant_product_id.is_not(None),
                )
                .group_by(BuyerEvent.merchant_product_id, "day")
            )
        else:
            d_stmt = (
                select(
                    BuyerEvent.merchant_product_id,
                    func.date_trunc("day", BuyerEvent.created_at).label("day"),
                    func.count().label("n"),
                )
                .where(
                    BuyerEvent.merchant_id == mid,
                    BuyerEvent.event_type == "impression",
                    BuyerEvent.created_at >= end - timedelta(days=7),
                    BuyerEvent.merchant_product_id.is_not(None),
                )
                .group_by(BuyerEvent.merchant_product_id, "day")
            )
        for pid, day, n in (await db.execute(d_stmt)).all():
            if pid in daily_impressions_by_product:
                parsed = _parse_day(day)
                if parsed:
                    idx = (end.date() - parsed).days
                    if 0 <= idx < 7:
                        daily_impressions_by_product[pid][6 - idx] = n
    except Exception as e:
        print(f"Error querying daily impressions in /products: {e}")

    # Fetch all leads in this window for the merchant to check containment
    leads_stmt = select(BuyerLead).where(
        BuyerLead.merchant_id == mid,
        BuyerLead.created_at.between(start, end),
    )
    leads = (await db.execute(leads_stmt)).scalars().all()

    # Fetch Order counts per product (payment_received = confirmed converted orders)
    from app.models.lead import Order, OrderStatus
    orders_stmt = (
        select(Order.merchant_product_id, func.count(Order.id).label("cnt"))
        .where(
            Order.merchant_id == mid,
            Order.status.in_([
                OrderStatus.PAYMENT_RECEIVED.value if hasattr(OrderStatus, 'PAYMENT_RECEIVED') else 'payment_received',
                OrderStatus.CONFIRMED.value if hasattr(OrderStatus, 'CONFIRMED') else 'confirmed',
            ])
        )
        .group_by(Order.merchant_product_id)
    )
    try:
        orders_by_product = {row[0]: row[1] for row in (await db.execute(orders_stmt)).all()}
    except Exception:
        orders_by_product = {}

    items = []
    for pid, p in products.items():
        c = counts_by_product.get(pid, {})
        imps = c.get("impression", 0)
        clicks = c.get("click", 0)

        # Filter leads that contain this product
        p_leads = [l for l in leads if str(pid) in l.product_ids]
        converted_count = sum(1 for l in p_leads if l.status == "converted")
        realized_revenue = sum(float(l.estimated_value or 0) for l in p_leads if l.status == "converted")

        spend = spend_by_product.get(pid, 0.0)
        est_roas = (realized_revenue / spend) if spend > 0 else 0.0

        orders_count = orders_by_product.get(pid, 0)
        est_ros = (orders_count / spend) if spend > 0 else 0.0

        # Generate trend message
        if p.status == "paused_insufficient_funds" or p.status == "archived":
            trend_desc = "Trend: Product is currently hidden from the AI app."
        elif imps > 100 and clicks == 0:
            trend_desc = "Trend: Zero clicks despite high impressions. Review image or description."
        elif (clicks / imps if imps else 0.0) > 0.05:
            trend_desc = "Trend: Strong CTR and conversion from core category queries."
        elif p.health_reason:
            trend_desc = f"Trend: {p.health_reason}"
        else:
            trend_desc = "Trend: Stable performance and regular shopper exposure."

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
                "spend": spend,
                "ctr": (clicks / imps) if imps else 0.0,
                "health_score": p.health_score,
                "category": p.category,
                "converted": converted_count,
                "est_roas": est_roas,
                "est_ros": est_ros,
                "orders_count": orders_count,
                "trend": trend_desc,
                "primary_image_url": p.primary_image_url,
                "daily_impressions": daily_impressions_by_product.get(pid, [0] * 7),
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

    is_sqlite = "sqlite" in get_settings().DATABASE_URL.lower()

    # Top RAG queries with conversion rates
    top_rag: list[dict] = []
    try:
        if is_sqlite:
            prompt_expr = func.json_extract(BuyerEvent.context, '$.prompt')
        else:
            prompt_expr = BuyerEvent.context["prompt"].astext

        rag_stmt = (
            select(
                prompt_expr.label("prompt"),
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

        for q_prompt, n in rag_rows:
            if not q_prompt:
                continue

            user_stmt = select(func.distinct(BuyerEvent.user_id)).where(
                BuyerEvent.merchant_product_id == product_id,
                BuyerEvent.event_type == "ai_rag_mention",
                prompt_expr == q_prompt,
            )
            users_res = await db.execute(user_stmt)
            user_ids = [row[0] for row in users_res.all()]

            if not user_ids:
                top_rag.append({"query": q_prompt[:200], "count": n, "conversion_rate": 0.0})
                continue

            if is_sqlite:
                lead_filter = BuyerLead.product_ids.like(f'%"{product_id}"%')
            else:
                lead_filter = BuyerLead.product_ids.contains([str(product_id)])

            converted_stmt = select(func.count(func.distinct(BuyerLead.user_id))).where(
                BuyerLead.user_id.in_(user_ids),
                BuyerLead.merchant_id == ctx.merchant.id,
                BuyerLead.status == "converted",
                lead_filter,
            )
            conv_users = (await db.execute(converted_stmt)).scalar_one()

            conversion_rate = (conv_users / len(user_ids)) if user_ids else 0.0
            top_rag.append({
                "query": q_prompt[:200],
                "count": n,
                "conversion_rate": conversion_rate
            })
    except Exception as e:
        print(f"Error calculating top rag queries for product details: {e}")

    # Daily impressions + clicks for last 7 days
    daily_imps = [0] * 7
    daily_clicks = [0] * 7
    for et, daily_arr in (("impression", daily_imps), ("click", daily_clicks)):
        try:
            if is_sqlite:
                d_stmt = (
                    select(
                        func.strftime("%Y-%m-%d", BuyerEvent.created_at).label("day"),
                        func.count(),
                    )
                    .where(
                        BuyerEvent.merchant_product_id == product_id,
                        BuyerEvent.event_type == et,
                        BuyerEvent.created_at >= end - timedelta(days=7),
                    )
                    .group_by("day")
                )
            else:
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
                parsed = _parse_day(day)
                if parsed:
                    idx = (end.date() - parsed).days
                    if 0 <= idx < 7:
                        daily_arr[6 - idx] = n
        except Exception:
            pass

    # Calculate product financial metrics
    if is_sqlite:
        lead_filter = BuyerLead.product_ids.like(f'%"{product_id}"%')
    else:
        lead_filter = BuyerLead.product_ids.contains([str(product_id)])

    leads_stmt = select(BuyerLead.status, BuyerLead.estimated_value).where(
        BuyerLead.merchant_id == ctx.merchant.id,
        lead_filter,
    )
    product_leads = (await db.execute(leads_stmt)).all()

    leads_count = len(product_leads)
    converted_count = sum(1 for status, _ in product_leads if status == "converted")

    realized_revenue = sum(float(val or 0) for status, val in product_leads if status == "converted")
    potential_pipeline = sum(float(val or 0) for status, val in product_leads if status in ("new", "synced"))

    cost_per_lead = (spend / leads_count) if leads_count > 0 else 0.0
    avg_sale = (realized_revenue / converted_count) if converted_count > 0 else 0.0
    token_roas = (realized_revenue / spend) if spend > 0 else 0.0

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
        "leads_count": leads_count,
        "converted_count": converted_count,
        "cost_per_lead": cost_per_lead,
        "avg_sale": avg_sale,
        "token_roas": token_roas,
        "realized_revenue": realized_revenue,
        "potential_pipeline": potential_pipeline,
        "primary_image_url": product.primary_image_url,
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

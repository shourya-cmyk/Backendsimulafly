"""Executive dashboard KPI / time-series aggregation (Requirement 5).

``DashboardService`` backs the admin ``dashboard.py`` router. It computes the
executive KPIs the Admin Panel renders (``Admin-Panel/app/(dashboard)/
executive/page.tsx``) from *real* backend tables, aggregated over the window a
``Time_Range`` resolves to via :func:`app.services.admin.time_range.resolve_time_range`.

Aggregation rules (mirroring the UI's ``isAverage(type, id)`` helper in
``Admin-Panel/lib/mock-data.ts``):

* **Sum-type** KPIs (``type`` ``currency``/``number`` that are not averages)
  are *summed* over the window (R5.1).
* **Rate/average/percentage** KPIs (``type='percentage'``, ``id='aov'``, or an
  id containing ``rate``/``avg``) are *averaged* over the window rather than
  summed (R5.5, R18.6).

Each KPI is returned as ``{id, title, type, value}``; ``currency`` values are
INR amounts (R5.3) because every money column in the schema is INR-denominated.
A ``compare=true`` request adds the prior-period value (the immediately
preceding window of equal length) per KPI (R5.7). The time-series endpoint
returns an ordered ``[{name, value}]`` series whose bucket count/labels match
the UI's ``generateChartData`` exactly (R5.4).

KPI registry & data sources
---------------------------
The :data:`KPI_REGISTRY` mirrors the UI's KPI ids/titles/types one-for-one. Each
KPI is computed by counting/summing rows whose timestamp falls inside the
resolved window, so an independent recomputation over the same rows yields the
same value (design Property 11). Where a metric has no first-class source table
it is *approximated* from the closest real signal and the approximation is
documented on the spec's ``note`` field (and summarised below):

* ``active_stores`` / ``new_stores`` — stores created in the window (a created
  ``Store`` row; ``active_stores`` additionally filters ``status='active'``).
* ``merchant_churn`` — suspended-merchant share of merchants created in window.
* ``users`` — active users created in the window (``new_signups`` drops the
  active filter).
* ``user_retention`` — active share of users created in the window.
* ``wallet_topups`` — successful wallet transactions (same source as revenue).
* ``ai_cost`` — wallet ``deduction`` ledger entries (the billed cost of AI ops).
* ``timeout_rate`` — no timeout signal is captured; reported as ``0`` until one
  exists.
* ``provider_uptime`` — delivered share of webhook deliveries; ``100`` when no
  deliveries occurred in the window.
* ``sla_breaches`` — tickets whose ``sla_due_at`` falls in the window and are
  not yet resolved.
* ``catalog_issues`` / ``missing_images`` — products created in window with a
  non-``good`` health score / no primary image.
* ``store_visits`` — ``click`` buyer-events (the closest visit signal).
* ``add_to_cart`` — cart items created in the window.
* ``purchases`` — orders that reached ``completed`` in the window.

The service is async and uses only portable SQL (a windowed ``SELECT`` of
``(timestamp, magnitude)`` rows), bucketing in Python, so it runs identically on
Postgres and the test SQLite engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable

from sqlalchemy import literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin import FraudAlert
from app.models.cart import CartItem
from app.models.event import BuyerEvent, EventType, LedgerEntry, LedgerEntryType
from app.models.lead import DisputeStatus, Order, OrderStatus
from app.models.merchant import Merchant, MerchantStatus
from app.models.merchant_product import MerchantProduct
from app.models.store import Store, StoreStatus
from app.models.support import SupportTicket, SupportTicketStatus
from app.models.user import User
from app.models.wallet import Transaction, TransactionStatus
from app.models.webhook_delivery import WebhookDelivery, WebhookDeliveryStatus
from app.schemas.admin.dashboard import (
    KPISeries,
    KPISeriesPoint,
    KPIValue,
    MetricType,
)
from app.services.admin.time_range import ResolvedRange, resolve_time_range

#: Aggregation modes. ``SUM``/``AVERAGE`` aggregate a list of magnitudes;
#: ``PERCENTAGE`` divides a numerator count by a denominator count.
_SUM = "sum"
_AVERAGE = "average"
_PERCENTAGE = "percentage"

#: A point source yields ``(timestamp, magnitude)`` rows within a window.
PointSource = Callable[[AsyncSession, datetime, datetime], Awaitable[list[tuple[datetime, float]]]]


def _to_utc(dt: datetime) -> datetime:
    """Normalise a (possibly naive) timestamp to a tz-aware UTC datetime."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _to_naive_utc(dt: datetime) -> datetime:
    """Coerce a window bound to a naive UTC ``datetime`` for SQL binding.

    Returns ``dt`` unchanged when already naive; otherwise converts to UTC and
    drops ``tzinfo``. Several domain tables (``users``, ``merchants``, orders,
    buyer events, ledger entries, …) declare ``created_at`` as ``TIMESTAMP
    WITHOUT TIME ZONE``, and asyncpg cannot bind a tz-aware datetime to a naive
    timestamp parameter. A naive-UTC bound is correct for those naive columns
    and is compared fine by Postgres for any tz-aware columns.
    """
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


async def _fetch(
    db: AsyncSession,
    ts_col,
    value_expr,
    start: datetime,
    end: datetime,
    *filters,
) -> list[tuple[datetime, float]]:
    """Return ``(timestamp, magnitude)`` rows in ``[start, end)`` for a metric.

    The window filter is applied in SQL (portable across Postgres/SQLite); the
    magnitude is coerced to ``float`` and the timestamp normalised to UTC so the
    caller can bucket purely in Python. The window bounds are coerced to naive
    UTC before binding so asyncpg can bind them to naive ``TIMESTAMP WITHOUT
    TIME ZONE`` columns; returned row timestamps are still normalised to
    tz-aware UTC so Python-side bucketing stays aware-vs-aware.
    """
    start = _to_naive_utc(start)
    end = _to_naive_utc(end)
    stmt = select(ts_col, value_expr).where(ts_col >= start, ts_col < end, *filters)
    rows = (await db.execute(stmt)).all()
    out: list[tuple[datetime, float]] = []
    for ts, val in rows:
        if ts is None:
            continue
        out.append((_to_utc(ts), float(val) if val is not None else 0.0))
    return out


def _count_source(model, ts_col, *filters) -> PointSource:
    """Build a point source that emits one unit-magnitude row per matching row."""

    async def _src(db: AsyncSession, start: datetime, end: datetime):
        return await _fetch(db, ts_col, literal(1), start, end, *filters)

    return _src


def _amount_source(ts_col, value_col, *filters) -> PointSource:
    """Build a point source whose magnitude is a numeric column (sum/avg)."""

    async def _src(db: AsyncSession, start: datetime, end: datetime):
        return await _fetch(db, ts_col, value_col, start, end, *filters)

    return _src


@dataclass(frozen=True)
class KPISpec:
    """Definition of a single executive KPI and how to aggregate it."""

    id: str
    title: str
    type: MetricType
    mode: str
    #: For SUM/AVERAGE modes: the magnitude source.
    source: PointSource | None = None
    #: For PERCENTAGE mode: numerator and denominator count sources.
    num_source: PointSource | None = None
    den_source: PointSource | None = None
    #: Percentage value to report when the denominator is empty.
    empty_percent: float = 0.0
    #: Optional note documenting an approximated data source.
    note: str | None = None


# --- KPI registry (mirrors Admin-Panel KPI ids/titles/types) ----------------
# Sources reference real tables; approximations are documented on ``note`` and
# in the module docstring. Built lazily as module-level constants so they are
# shared across requests.

_SUCCESSFUL = Transaction.status == TransactionStatus.SUCCESSFUL.value

KPI_REGISTRY: dict[str, KPISpec] = {
    # ── Commerce KPIs ──
    "revenue": KPISpec(
        "revenue", "Total Revenue", "currency", _SUM,
        source=_amount_source(Transaction.created_at, Transaction.amount, _SUCCESSFUL),
    ),
    "orders": KPISpec(
        "orders", "Total Orders", "number", _SUM,
        source=_count_source(Order, Order.created_at),
    ),
    "aov": KPISpec(
        "aov", "Avg Order Value", "currency", _AVERAGE,
        source=_amount_source(Order.created_at, Order.total_estimated),
    ),
    "gmv": KPISpec(
        "gmv", "Gross Merchandise Value", "currency", _SUM,
        source=_amount_source(Order.created_at, Order.total_estimated),
    ),
    # ── Merchant KPIs ──
    "total_merchants": KPISpec(
        "total_merchants", "Total Merchants", "number", _SUM,
        source=_count_source(Merchant, Merchant.created_at),
        note="New merchants created within the window.",
    ),
    "active_stores": KPISpec(
        "active_stores", "Active Stores", "number", _SUM,
        source=_count_source(Store, Store.created_at, Store.status == StoreStatus.ACTIVE.value),
        note="Active stores created within the window.",
    ),
    "new_stores": KPISpec(
        "new_stores", "New Stores", "number", _SUM,
        source=_count_source(Store, Store.created_at),
    ),
    "merchant_churn": KPISpec(
        "merchant_churn", "Merchant Churn", "percentage", _PERCENTAGE,
        num_source=_count_source(
            Merchant, Merchant.created_at, Merchant.status == MerchantStatus.SUSPENDED.value
        ),
        den_source=_count_source(Merchant, Merchant.created_at),
        note="Suspended-merchant share of merchants created within the window.",
    ),
    # ── User KPIs ──
    "users": KPISpec(
        "users", "Active Users", "number", _SUM,
        source=_count_source(User, User.created_at, User.is_active.is_(True)),
        note="Active users created within the window.",
    ),
    "new_signups": KPISpec(
        "new_signups", "New Signups", "number", _SUM,
        source=_count_source(User, User.created_at),
    ),
    "wallet_topups": KPISpec(
        "wallet_topups", "Wallet Topups", "currency", _SUM,
        source=_amount_source(Transaction.created_at, Transaction.amount, _SUCCESSFUL),
        note="Successful wallet transactions (shares revenue's source).",
    ),
    "user_retention": KPISpec(
        "user_retention", "User Retention", "percentage", _PERCENTAGE,
        num_source=_count_source(User, User.created_at, User.is_active.is_(True)),
        den_source=_count_source(User, User.created_at),
        note="Active share of users created within the window.",
    ),
    # ── AI Economics ──
    "ai": KPISpec(
        "ai", "AI Operations", "number", _SUM,
        source=_count_source(
            BuyerEvent, BuyerEvent.created_at,
            BuyerEvent.event_type == EventType.AI_IMAGE_GENERATION.value,
        ),
    ),
    "ai_cost": KPISpec(
        "ai_cost", "Total AI Cost", "currency", _SUM,
        source=_amount_source(
            LedgerEntry.created_at, LedgerEntry.amount,
            LedgerEntry.entry_type == LedgerEntryType.DEDUCTION.value,
        ),
        note="Wallet deduction ledger entries (billed cost of operations).",
    ),
    "timeout_rate": KPISpec(
        "timeout_rate", "Timeout Rate", "percentage", _PERCENTAGE,
        num_source=_count_source(BuyerEvent, BuyerEvent.created_at, literal(False)),
        den_source=_count_source(
            BuyerEvent, BuyerEvent.created_at,
            BuyerEvent.event_type == EventType.AI_IMAGE_GENERATION.value,
        ),
        empty_percent=0.0,
        note="No timeout signal captured; reported as 0 until one exists.",
    ),
    "provider_uptime": KPISpec(
        "provider_uptime", "Provider Uptime", "percentage", _PERCENTAGE,
        num_source=_count_source(
            WebhookDelivery, WebhookDelivery.created_at,
            WebhookDelivery.status == WebhookDeliveryStatus.DELIVERED.value,
        ),
        den_source=_count_source(WebhookDelivery, WebhookDelivery.created_at),
        empty_percent=100.0,
        note="Delivered share of webhook deliveries; 100 when none occurred.",
    ),
    # ── Merchant Intervention ──
    "fraud_cases": KPISpec(
        "fraud_cases", "Fraud Cases", "number", _SUM,
        source=_count_source(FraudAlert, FraudAlert.created_at),
    ),
    "disputes": KPISpec(
        "disputes", "Disputed Invoices", "number", _SUM,
        source=_count_source(
            Order, Order.created_at, Order.dispute_status != DisputeStatus.NONE.value
        ),
        note="Orders with an open/resolved dispute created within the window.",
    ),
    "sla_breaches": KPISpec(
        "sla_breaches", "SLA Breaches", "number", _SUM,
        source=_count_source(
            SupportTicket, SupportTicket.sla_due_at,
            SupportTicket.status != SupportTicketStatus.RESOLVED.value,
        ),
        note="Unresolved tickets whose SLA due time falls within the window.",
    ),
    "support_tickets": KPISpec(
        "support_tickets", "Support Tickets", "number", _SUM,
        source=_count_source(SupportTicket, SupportTicket.created_at),
    ),
    # ── Store Health ──
    "stores_online": KPISpec(
        "stores_online", "Stores Online", "percentage", _PERCENTAGE,
        num_source=_count_source(Store, Store.created_at, Store.status == StoreStatus.ACTIVE.value),
        den_source=_count_source(Store, Store.created_at),
        empty_percent=100.0,
        note="Active share of stores created within the window.",
    ),
    "catalog_issues": KPISpec(
        "catalog_issues", "Catalog Issues", "number", _SUM,
        source=_count_source(
            MerchantProduct, MerchantProduct.created_at, MerchantProduct.health_score != "good"
        ),
        note="Products created in window with a non-good health score.",
    ),
    "missing_images": KPISpec(
        "missing_images", "Missing Images", "number", _SUM,
        source=_count_source(
            MerchantProduct, MerchantProduct.created_at, MerchantProduct.primary_image_url.is_(None)
        ),
        note="Products created in window with no primary image.",
    ),
    "sync_failures": KPISpec(
        "sync_failures", "Sync Failures", "number", _SUM,
        source=_count_source(
            WebhookDelivery, WebhookDelivery.created_at,
            WebhookDelivery.status == WebhookDeliveryStatus.FAILED.value,
        ),
        note="Failed webhook deliveries within the window.",
    ),
    # ── Conversion Drop-off ──
    "impressions": KPISpec(
        "impressions", "Impressions", "number", _SUM,
        source=_count_source(
            BuyerEvent, BuyerEvent.created_at, BuyerEvent.event_type == EventType.IMPRESSION.value
        ),
    ),
    "store_visits": KPISpec(
        "store_visits", "Store Visits", "number", _SUM,
        source=_count_source(
            BuyerEvent, BuyerEvent.created_at, BuyerEvent.event_type == EventType.CLICK.value
        ),
        note="Click buyer-events (closest available visit signal).",
    ),
    "add_to_cart": KPISpec(
        "add_to_cart", "Add to Cart", "number", _SUM,
        source=_count_source(CartItem, CartItem.added_at),
        note="Cart items created within the window.",
    ),
    "purchases": KPISpec(
        "purchases", "Purchases", "number", _SUM,
        source=_count_source(
            Order, Order.created_at, Order.status == OrderStatus.COMPLETED.value
        ),
        note="Orders that reached completed within the window.",
    ),
}


def _round_value(metric_type: str, value: float) -> float:
    """Round a value per its Metric_Type (INR to paise, % to 0.1, counts whole)."""
    if metric_type == "currency":
        return round(value, 2)
    if metric_type == "percentage":
        return round(value, 1)
    return float(round(value))


def _aggregate(spec: KPISpec, points: list[tuple[datetime, float]]) -> float:
    """Aggregate sum/average magnitude points into a single value."""
    if spec.mode == _SUM:
        return sum(m for _, m in points)
    # AVERAGE
    if not points:
        return 0.0
    return sum(m for _, m in points) / len(points)


def _percentage(spec: KPISpec, num: int, den: int) -> float:
    """Compute a percentage value, honouring the empty-denominator default."""
    if den == 0:
        return spec.empty_percent
    return 100.0 * num / den


def _bucket_index(ts: datetime, resolved: ResolvedRange) -> int:
    """Map a timestamp to its bucket index in ``[0, bucket_count)``."""
    width = resolved.duration / resolved.bucket_count
    offset = ts - resolved.window_start
    idx = int(offset / width)
    if idx < 0:
        return 0
    if idx >= resolved.bucket_count:
        return resolved.bucket_count - 1
    return idx


class DashboardService:
    """Executive dashboard KPI/series aggregation backing ``dashboard`` router (R5)."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # -- KPIs --------------------------------------------------------------

    async def executive_kpis(
        self,
        range_str: str | None,
        *,
        compare: bool = False,
        now: datetime | None = None,
    ) -> list[KPIValue]:
        """Aggregate every executive KPI over the resolved window (R5.1–R5.3, R5.7).

        An invalid/missing ``range_str`` raises
        :class:`~app.services.admin.time_range.InvalidTimeRangeError` (the router
        maps it to HTTP 422). When ``compare`` is set, each KPI also carries its
        prior-period value (R5.7).
        """
        resolved = resolve_time_range(range_str, now=now)
        prior: ResolvedRange | None = None
        if compare:
            prior = resolve_time_range(range_str, now=resolved.window_start)

        results: list[KPIValue] = []
        for spec in KPI_REGISTRY.values():
            value = await self._kpi_value(spec, resolved.window_start, resolved.window_end)
            prior_value: float | None = None
            if prior is not None:
                prior_value = await self._kpi_value(
                    spec, prior.window_start, prior.window_end
                )
            results.append(
                KPIValue(
                    id=spec.id,
                    title=spec.title,
                    type=spec.type,
                    value=_round_value(spec.type, value),
                    prior=None if prior_value is None else _round_value(spec.type, prior_value),
                )
            )
        return results

    async def _kpi_value(self, spec: KPISpec, start: datetime, end: datetime) -> float:
        """Compute a single KPI's aggregated value over ``[start, end)``."""
        if spec.mode == _PERCENTAGE:
            num_points = await spec.num_source(self.db, start, end)  # type: ignore[misc]
            den_points = await spec.den_source(self.db, start, end)  # type: ignore[misc]
            return _percentage(spec, len(num_points), len(den_points))
        points = await spec.source(self.db, start, end)  # type: ignore[misc]
        return _aggregate(spec, points)

    # -- Time-series -------------------------------------------------------

    async def kpi_series(
        self,
        kpi_id: str,
        range_str: str | None,
        *,
        now: datetime | None = None,
    ) -> KPISeries | None:
        """Return the ordered time-series for one KPI over the window (R5.4, R5.5).

        Returns ``None`` when ``kpi_id`` is unknown (the router maps it to HTTP
        404); an invalid ``range_str`` raises ``InvalidTimeRangeError`` (→ 422).
        Sum-type KPIs are summed per bucket; rate/average/percentage KPIs are
        averaged per bucket (R5.5).
        """
        spec = KPI_REGISTRY.get(kpi_id)
        if spec is None:
            return None
        resolved = resolve_time_range(range_str, now=now)

        if spec.mode == _PERCENTAGE:
            series = await self._percentage_series(spec, resolved)
        else:
            series = await self._magnitude_series(spec, resolved)

        return KPISeries(
            id=spec.id,
            title=spec.title,
            type=spec.type,
            range=resolved.value,
            series=series,
        )

    async def _magnitude_series(
        self, spec: KPISpec, resolved: ResolvedRange
    ) -> list[KPISeriesPoint]:
        """Bucket sum/average magnitude points into an ordered series."""
        points = await spec.source(self.db, resolved.window_start, resolved.window_end)  # type: ignore[misc]
        buckets: list[list[float]] = [[] for _ in range(resolved.bucket_count)]
        for ts, magnitude in points:
            buckets[_bucket_index(ts, resolved)].append(magnitude)

        series: list[KPISeriesPoint] = []
        for idx, label in enumerate(resolved.bucket_labels):
            bucket = buckets[idx]
            if spec.mode == _AVERAGE:
                value = sum(bucket) / len(bucket) if bucket else 0.0
            else:
                value = sum(bucket)
            series.append(KPISeriesPoint(name=label, value=_round_value(spec.type, value)))
        return series

    async def _percentage_series(
        self, spec: KPISpec, resolved: ResolvedRange
    ) -> list[KPISeriesPoint]:
        """Bucket numerator/denominator counts into a per-bucket percentage series."""
        num_points = await spec.num_source(self.db, resolved.window_start, resolved.window_end)  # type: ignore[misc]
        den_points = await spec.den_source(self.db, resolved.window_start, resolved.window_end)  # type: ignore[misc]
        num_counts = [0] * resolved.bucket_count
        den_counts = [0] * resolved.bucket_count
        for ts, _ in num_points:
            num_counts[_bucket_index(ts, resolved)] += 1
        for ts, _ in den_points:
            den_counts[_bucket_index(ts, resolved)] += 1

        series: list[KPISeriesPoint] = []
        for idx, label in enumerate(resolved.bucket_labels):
            value = _percentage(spec, num_counts[idx], den_counts[idx])
            series.append(KPISeriesPoint(name=label, value=_round_value(spec.type, value)))
        return series

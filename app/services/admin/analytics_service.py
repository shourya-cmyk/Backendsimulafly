"""Analytics views aggregation (Requirement 18).

``AnalyticsService`` backs the admin ``analytics.py`` router. It serves the four
analytics surfaces the Admin Panel renders from *real* backend tables,
aggregated over the window a ``Time_Range`` resolves to via
:func:`app.services.admin.time_range.resolve_time_range`:

* **user-activity** (R18.1) — user engagement: active users / new signups,
  engagement-event volume, click-through rate, and average user credit balance.
* **merchant-activity** (R18.2) — merchant engagement: total / active merchants,
  order volume, GMV, average order value, and active-merchant rate.
* **wallet-referral** (R18.3) — wallet recharge / spend, transaction volume,
  average recharge value, referred signups, and referral-conversion rate.
* **ai-data-usage** (R18.4) — AI generation / failure counts, AI-failure rate,
  AI (billed) cost, average AI cost per operation, and data-event volume.

Aggregation rules (mirroring :mod:`app.services.admin.dashboard_service` /
:mod:`app.services.admin.finance_service`):

* **Sum-type** metrics (``type`` ``currency``/``number`` aggregated by sum) are
  *summed* over the window.
* **Rate/average/percentage** metrics are *averaged* over the window rather than
  summed (R18.6) — ``AVERAGE`` metrics are the mean of a numeric magnitude and
  ``PERCENTAGE`` metrics are a numerator/denominator count ratio. Both are
  scale-invariant, so duplicating identical rows leaves the value unchanged.

Each metric is returned as ``{id, title, type, value[, note]}``; ``currency``
values are INR amounts because every money column in the schema is
INR-denominated. An invalid/missing ``Time_Range`` raises
:class:`~app.services.admin.time_range.InvalidTimeRangeError` (the router maps it
to HTTP 422 — R18.5).

Approximated metrics & data sources
-----------------------------------
Where a metric has no first-class source it is approximated from the closest
real signal and documented on the metric's ``note`` field (and summarised here):

* ``engagement_events`` / ``data_events`` — all ``BuyerEvent`` rows in the
  window (the closest engagement / data-usage volume signal).
* ``click_through_rate`` — ``click`` events as a share of ``impression`` events.
* ``avg_credit_balance`` — average ``User.credit_balance`` over users *created*
  in the window (windowed by ``created_at`` to stay deterministic and
  SQLite-portable, mirroring the dashboard's created-in-window approach).
* ``wallet_spend`` / ``ai_cost`` — ``deduction`` ledger entries (the billed cost
  of AI operations is the platform's wallet spend / data-usage cost).
* ``referred_signups`` / ``referral_conversion_rate`` — users created in the
  window whose ``referred_by_code`` is set, over all users created in window.
* ``ai_failures`` / ``ai_failure_rate`` — no AI-failure signal is captured, so
  the failure count is reported as ``0`` (and the rate as ``0``) until one
  exists.

The service is async and uses only portable SQL (a windowed ``SELECT`` of
``(timestamp, magnitude)`` rows), aggregating in Python, so it runs identically
on Postgres and the test SQLite engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable

from sqlalchemy import literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event import BuyerEvent, EventType, LedgerEntry, LedgerEntryType
from app.models.lead import Order
from app.models.merchant import Merchant, MerchantStatus
from app.models.user import User
from app.models.wallet import Transaction, TransactionStatus
from app.schemas.admin.analytics import AnalyticsMetric, AnalyticsView, MetricType
from app.services.admin.time_range import resolve_time_range

#: Aggregation modes. ``SUM``/``AVERAGE`` aggregate a list of magnitudes;
#: ``PERCENTAGE`` divides a numerator count by a denominator count.
_SUM = "sum"
_AVERAGE = "average"
_PERCENTAGE = "percentage"

#: Successful-transaction filter, shared by recharge metrics.
_SUCCESSFUL = Transaction.status == TransactionStatus.SUCCESSFUL.value
#: Wallet deduction filter (billed AI / data-usage cost == platform spend).
_DEDUCTION = LedgerEntry.entry_type == LedgerEntryType.DEDUCTION.value

#: A point source yields ``(timestamp, magnitude)`` rows within a window.
PointSource = Callable[
    [AsyncSession, datetime, datetime], Awaitable[list[tuple[datetime, float]]]
]


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
    caller can aggregate purely in Python. The window bounds are coerced to
    naive UTC before binding so asyncpg can bind them to naive ``TIMESTAMP
    WITHOUT TIME ZONE`` columns; returned row timestamps are still normalised to
    tz-aware UTC so Python-side aggregation/bucketing stays aware-vs-aware.
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


def _count_source(ts_col, *filters) -> PointSource:
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
class AnalyticsMetricSpec:
    """Definition of a single analytics metric and how to aggregate it."""

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


# --- View registries (each maps real tables; approximations on ``note``) -----

#: user-activity (R18.1) — user engagement metrics.
_USER_ACTIVITY: tuple[AnalyticsMetricSpec, ...] = (
    AnalyticsMetricSpec(
        "active_users", "Active Users", "number", _SUM,
        source=_count_source(User.created_at, User.is_active.is_(True)),
        note="Active users created within the window.",
    ),
    AnalyticsMetricSpec(
        "new_signups", "New Signups", "number", _SUM,
        source=_count_source(User.created_at),
    ),
    AnalyticsMetricSpec(
        "engagement_events", "Engagement Events", "number", _SUM,
        source=_count_source(BuyerEvent.created_at),
        note="All buyer events in the window (engagement volume signal).",
    ),
    AnalyticsMetricSpec(
        "clicks", "Clicks", "number", _SUM,
        source=_count_source(
            BuyerEvent.created_at, BuyerEvent.event_type == EventType.CLICK.value
        ),
    ),
    AnalyticsMetricSpec(
        "click_through_rate", "Click-Through Rate", "percentage", _PERCENTAGE,
        num_source=_count_source(
            BuyerEvent.created_at, BuyerEvent.event_type == EventType.CLICK.value
        ),
        den_source=_count_source(
            BuyerEvent.created_at, BuyerEvent.event_type == EventType.IMPRESSION.value
        ),
        empty_percent=0.0,
        note="Click events as a share of impression events (averaged rate).",
    ),
    AnalyticsMetricSpec(
        "avg_credit_balance", "Avg Credit Balance", "number", _AVERAGE,
        source=_amount_source(User.created_at, User.credit_balance),
        note="Average credit balance over users created within the window.",
    ),
)

#: merchant-activity (R18.2) — merchant engagement metrics.
_MERCHANT_ACTIVITY: tuple[AnalyticsMetricSpec, ...] = (
    AnalyticsMetricSpec(
        "total_merchants", "Total Merchants", "number", _SUM,
        source=_count_source(Merchant.created_at),
        note="Merchants created within the window.",
    ),
    AnalyticsMetricSpec(
        "active_merchants", "Active Merchants", "number", _SUM,
        source=_count_source(
            Merchant.created_at, Merchant.status == MerchantStatus.ACTIVE.value
        ),
        note="Active merchants created within the window.",
    ),
    AnalyticsMetricSpec(
        "orders", "Orders", "number", _SUM,
        source=_count_source(Order.created_at),
    ),
    AnalyticsMetricSpec(
        "gmv", "Gross Merchandise Value", "currency", _SUM,
        source=_amount_source(Order.created_at, Order.total_estimated),
        note="Sum of order estimated values within the window (INR).",
    ),
    AnalyticsMetricSpec(
        "avg_order_value", "Avg Order Value", "currency", _AVERAGE,
        source=_amount_source(Order.created_at, Order.total_estimated),
        note="Average order estimated value within the window (INR).",
    ),
    AnalyticsMetricSpec(
        "active_merchant_rate", "Active Merchant Rate", "percentage", _PERCENTAGE,
        num_source=_count_source(
            Merchant.created_at, Merchant.status == MerchantStatus.ACTIVE.value
        ),
        den_source=_count_source(Merchant.created_at),
        empty_percent=0.0,
        note="Active share of merchants created within the window (averaged rate).",
    ),
)

#: wallet-referral (R18.3) — wallet recharge / spend / referral conversion.
_WALLET_REFERRAL: tuple[AnalyticsMetricSpec, ...] = (
    AnalyticsMetricSpec(
        "wallet_recharge", "Wallet Recharge", "currency", _SUM,
        source=_amount_source(Transaction.created_at, Transaction.amount, _SUCCESSFUL),
        note="Sum of successful transaction amounts within the window (INR).",
    ),
    AnalyticsMetricSpec(
        "wallet_spend", "Wallet Spend", "currency", _SUM,
        source=_amount_source(LedgerEntry.created_at, LedgerEntry.amount, _DEDUCTION),
        note="Sum of wallet deduction ledger entries within the window (INR).",
    ),
    AnalyticsMetricSpec(
        "transactions", "Transactions", "number", _SUM,
        source=_count_source(Transaction.created_at),
    ),
    AnalyticsMetricSpec(
        "avg_recharge_value", "Avg Recharge Value", "currency", _AVERAGE,
        source=_amount_source(Transaction.created_at, Transaction.amount, _SUCCESSFUL),
        note="Average successful transaction amount within the window (INR).",
    ),
    AnalyticsMetricSpec(
        "referred_signups", "Referred Signups", "number", _SUM,
        source=_count_source(User.created_at, User.referred_by_code.isnot(None)),
        note="Users created within the window who used a referral code.",
    ),
    AnalyticsMetricSpec(
        "referral_conversion_rate", "Referral Conversion Rate", "percentage", _PERCENTAGE,
        num_source=_count_source(User.created_at, User.referred_by_code.isnot(None)),
        den_source=_count_source(User.created_at),
        empty_percent=0.0,
        note="Referred share of users created within the window (averaged rate).",
    ),
)

#: ai-data-usage (R18.4) — AI generation / failure / data-usage metrics.
_AI_DATA_USAGE: tuple[AnalyticsMetricSpec, ...] = (
    AnalyticsMetricSpec(
        "ai_generations", "AI Generations", "number", _SUM,
        source=_count_source(
            BuyerEvent.created_at,
            BuyerEvent.event_type == EventType.AI_IMAGE_GENERATION.value,
        ),
    ),
    AnalyticsMetricSpec(
        "ai_failures", "AI Failures", "number", _SUM,
        source=_count_source(BuyerEvent.created_at, literal(False)),
        note="No AI-failure signal captured; reported as 0 until one exists.",
    ),
    AnalyticsMetricSpec(
        "ai_failure_rate", "AI Failure Rate", "percentage", _PERCENTAGE,
        num_source=_count_source(BuyerEvent.created_at, literal(False)),
        den_source=_count_source(
            BuyerEvent.created_at,
            BuyerEvent.event_type == EventType.AI_IMAGE_GENERATION.value,
        ),
        empty_percent=0.0,
        note="Failed AI operations as a share of generations; 0 until a failure signal exists.",
    ),
    AnalyticsMetricSpec(
        "ai_cost", "Total AI Cost", "currency", _SUM,
        source=_amount_source(LedgerEntry.created_at, LedgerEntry.amount, _DEDUCTION),
        note="Wallet deduction ledger entries — the billed cost of AI operations (INR).",
    ),
    AnalyticsMetricSpec(
        "avg_ai_cost", "Avg AI Cost", "currency", _AVERAGE,
        source=_amount_source(LedgerEntry.created_at, LedgerEntry.amount, _DEDUCTION),
        note="Average wallet deduction amount per operation within the window (INR).",
    ),
    AnalyticsMetricSpec(
        "data_events", "Data Events", "number", _SUM,
        source=_count_source(BuyerEvent.created_at),
        note="All buyer events in the window (data-usage volume signal).",
    ),
)

#: All analytics views keyed by their URL slug.
ANALYTICS_VIEWS: dict[str, tuple[AnalyticsMetricSpec, ...]] = {
    "user-activity": _USER_ACTIVITY,
    "merchant-activity": _MERCHANT_ACTIVITY,
    "wallet-referral": _WALLET_REFERRAL,
    "ai-data-usage": _AI_DATA_USAGE,
}


def _round_value(metric_type: str, value: float) -> float:
    """Round a value per its Metric_Type (INR to paise, % to 0.1, counts whole)."""
    if metric_type == "currency":
        return round(value, 2)
    if metric_type == "percentage":
        return round(value, 1)
    return float(round(value))


def _aggregate(spec: AnalyticsMetricSpec, points: list[tuple[datetime, float]]) -> float:
    """Aggregate sum/average magnitude points into a single value."""
    if spec.mode == _SUM:
        return sum(m for _, m in points)
    # AVERAGE
    if not points:
        return 0.0
    return sum(m for _, m in points) / len(points)


def _percentage(spec: AnalyticsMetricSpec, num: int, den: int) -> float:
    """Compute a percentage value, honouring the empty-denominator default."""
    if den == 0:
        return spec.empty_percent
    return 100.0 * num / den


class AnalyticsService:
    """Analytics views aggregation backing the ``analytics`` router (R18)."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def view(
        self,
        view_name: str,
        range_str: str | None,
        *,
        now: datetime | None = None,
    ) -> AnalyticsView:
        """Aggregate every metric of ``view_name`` over the resolved window.

        Sum-type metrics are summed; rate/average/percentage metrics are
        averaged over the window rather than summed (R18.6). An invalid/missing
        ``range_str`` raises
        :class:`~app.services.admin.time_range.InvalidTimeRangeError` (the router
        maps it to HTTP 422 — R18.5).

        ``view_name`` must be one of :data:`ANALYTICS_VIEWS`; callers (the
        dedicated router endpoints) always pass a known slug.
        """
        specs = ANALYTICS_VIEWS[view_name]
        resolved = resolve_time_range(range_str, now=now)

        metrics: list[AnalyticsMetric] = []
        for spec in specs:
            value = await self._metric_value(
                spec, resolved.window_start, resolved.window_end
            )
            metrics.append(
                AnalyticsMetric(
                    id=spec.id,
                    title=spec.title,
                    type=spec.type,
                    value=_round_value(spec.type, value),
                    note=spec.note,
                )
            )
        return AnalyticsView(view=view_name, range=resolved.value, metrics=metrics)

    async def _metric_value(
        self, spec: AnalyticsMetricSpec, start: datetime, end: datetime
    ) -> float:
        """Compute a single metric's aggregated value over ``[start, end)``."""
        if spec.mode == _PERCENTAGE:
            num_points = await spec.num_source(self.db, start, end)  # type: ignore[misc]
            den_points = await spec.den_source(self.db, start, end)  # type: ignore[misc]
            return _percentage(spec, len(num_points), len(den_points))
        points = await spec.source(self.db, start, end)  # type: ignore[misc]
        return _aggregate(spec, points)

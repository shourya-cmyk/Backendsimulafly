"""Financial dashboard aggregation (Requirement 12).

``FinanceService`` backs the admin ``finance.py`` router. It computes the
financial KPIs the Admin Panel renders from *real* backend tables
(``app/models/wallet.py``: :class:`Transaction`, :class:`Wallet`), aggregated
over the window a ``Time_Range`` resolves to via
:func:`app.services.admin.time_range.resolve_time_range`.

Three read surfaces (mirroring :mod:`app.services.admin.dashboard_service`):

* **KPIs** (R12.1, R12.2) — revenue, transaction volume, and wallet-balance
  metrics aggregated over the window. Every monetary metric is an INR amount
  (R12.2) because every money column in the schema is INR-denominated.
* **Time-series** (R12.3) — an ordered ``[{name, value}]`` revenue series whose
  bucket count/labels match the UI's ``generateChartData`` exactly.
* **Transactions breakdown** (R12.5) — counts grouped by ``Transaction.status``
  whose group counts sum to the total transaction count (design Property 36).

KPI registry & data sources
---------------------------
* ``revenue`` — sum of successful (``status='successful'``) ``Transaction.amount``
  rows created in the window.
* ``transaction_volume`` — count of all ``Transaction`` rows created in window.
* ``successful_transactions`` — count of successful transactions in window.
* ``avg_transaction_value`` — average successful ``Transaction.amount`` in window.
* ``wallet_balance`` — sum of ``Wallet.balance`` over wallets created in window
  (windowed by ``created_at`` to mirror the dashboard's created-in-window
  approach so the metric is deterministic and SQLite-portable).
* ``avg_wallet_balance`` — average ``Wallet.balance`` over wallets created in
  window.

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

from app.models.wallet import Transaction, TransactionStatus, Wallet
from app.schemas.admin.finance import (
    FinanceKPI,
    FinanceSeries,
    FinanceSeriesPoint,
    MetricType,
    TransactionBreakdown,
    TransactionStatusCount,
)
from app.services.admin.time_range import ResolvedRange, resolve_time_range

#: Aggregation modes for a sum/average magnitude metric.
_SUM = "sum"
_AVERAGE = "average"

#: Successful-transaction filter, shared by revenue / avg-value metrics.
_SUCCESSFUL = Transaction.status == TransactionStatus.SUCCESSFUL.value

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
    drops ``tzinfo``. The ``wallet``/``transaction`` tables declare ``created_at``
    as ``TIMESTAMP WITHOUT TIME ZONE``, and asyncpg cannot bind a tz-aware
    datetime to a naive timestamp parameter. A naive-UTC bound is correct for
    those naive columns and is compared fine by Postgres for any tz-aware
    columns.
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
class FinanceKPISpec:
    """Definition of a single financial KPI and how to aggregate it."""

    id: str
    title: str
    type: MetricType
    mode: str
    source: PointSource
    note: str | None = None


#: Finance KPI registry. Sources reference real ``wallet.py`` tables; wallet
#: metrics are windowed by ``created_at`` (documented on ``note``).
FINANCE_KPI_REGISTRY: dict[str, FinanceKPISpec] = {
    "revenue": FinanceKPISpec(
        "revenue", "Total Revenue", "currency", _SUM,
        _amount_source(Transaction.created_at, Transaction.amount, _SUCCESSFUL),
        note="Sum of successful transaction amounts within the window (INR).",
    ),
    "transaction_volume": FinanceKPISpec(
        "transaction_volume", "Transaction Volume", "number", _SUM,
        _count_source(Transaction.created_at),
        note="Count of all transactions created within the window.",
    ),
    "successful_transactions": FinanceKPISpec(
        "successful_transactions", "Successful Transactions", "number", _SUM,
        _count_source(Transaction.created_at, _SUCCESSFUL),
        note="Count of successful transactions within the window.",
    ),
    "avg_transaction_value": FinanceKPISpec(
        "avg_transaction_value", "Avg Transaction Value", "currency", _AVERAGE,
        _amount_source(Transaction.created_at, Transaction.amount, _SUCCESSFUL),
        note="Average successful transaction amount within the window (INR).",
    ),
    "wallet_balance": FinanceKPISpec(
        "wallet_balance", "Wallet Balance", "currency", _SUM,
        _amount_source(Wallet.created_at, Wallet.balance),
        note="Sum of wallet balances for wallets created within the window (INR).",
    ),
    "avg_wallet_balance": FinanceKPISpec(
        "avg_wallet_balance", "Avg Wallet Balance", "currency", _AVERAGE,
        _amount_source(Wallet.created_at, Wallet.balance),
        note="Average wallet balance for wallets created within the window (INR).",
    ),
}

#: The metric whose time-series the dashboard series endpoint returns (R12.3).
DEFAULT_SERIES_KPI = "revenue"


def _round_value(metric_type: str, value: float) -> float:
    """Round a value per its Metric_Type (INR to paise, % to 0.1, counts whole)."""
    if metric_type == "currency":
        return round(value, 2)
    if metric_type == "percentage":
        return round(value, 1)
    return float(round(value))


def _aggregate(spec: FinanceKPISpec, points: list[tuple[datetime, float]]) -> float:
    """Aggregate sum/average magnitude points into a single value."""
    if spec.mode == _SUM:
        return sum(m for _, m in points)
    # AVERAGE
    if not points:
        return 0.0
    return sum(m for _, m in points) / len(points)


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


class FinanceService:
    """Financial dashboard KPI/series/breakdown aggregation (R12)."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # -- KPIs --------------------------------------------------------------

    async def finance_kpis(
        self,
        range_str: str | None,
        *,
        now: datetime | None = None,
    ) -> list[FinanceKPI]:
        """Aggregate every financial KPI over the resolved window (R12.1, R12.2).

        An invalid/missing ``range_str`` raises
        :class:`~app.services.admin.time_range.InvalidTimeRangeError` (the router
        maps it to HTTP 422 — R12.4). All ``currency`` values are INR (R12.2).
        """
        resolved = resolve_time_range(range_str, now=now)
        results: list[FinanceKPI] = []
        for spec in FINANCE_KPI_REGISTRY.values():
            points = await spec.source(self.db, resolved.window_start, resolved.window_end)
            value = _aggregate(spec, points)
            results.append(
                FinanceKPI(
                    id=spec.id,
                    title=spec.title,
                    type=spec.type,
                    value=_round_value(spec.type, value),
                )
            )
        return results

    # -- Time-series -------------------------------------------------------

    async def finance_series(
        self,
        range_str: str | None,
        *,
        kpi_id: str = DEFAULT_SERIES_KPI,
        now: datetime | None = None,
    ) -> FinanceSeries | None:
        """Return the ordered financial time-series over the window (R12.3).

        Defaults to the ``revenue`` metric. Returns ``None`` when ``kpi_id`` is
        unknown (the router maps it to HTTP 404); an invalid ``range_str`` raises
        ``InvalidTimeRangeError`` (→ 422 — R12.4). Sum-type metrics are summed
        per bucket; average metrics are averaged per bucket.
        """
        spec = FINANCE_KPI_REGISTRY.get(kpi_id)
        if spec is None:
            return None
        resolved = resolve_time_range(range_str, now=now)

        points = await spec.source(self.db, resolved.window_start, resolved.window_end)
        buckets: list[list[float]] = [[] for _ in range(resolved.bucket_count)]
        for ts, magnitude in points:
            buckets[_bucket_index(ts, resolved)].append(magnitude)

        series: list[FinanceSeriesPoint] = []
        for idx, label in enumerate(resolved.bucket_labels):
            bucket = buckets[idx]
            if spec.mode == _AVERAGE:
                value = sum(bucket) / len(bucket) if bucket else 0.0
            else:
                value = sum(bucket)
            series.append(
                FinanceSeriesPoint(name=label, value=_round_value(spec.type, value))
            )

        return FinanceSeries(
            id=spec.id,
            title=spec.title,
            type=spec.type,
            range=resolved.value,
            series=series,
        )

    # -- Transactions breakdown -------------------------------------------

    async def transactions_breakdown(self) -> TransactionBreakdown:
        """Return transaction counts grouped by ``Transaction.status`` (R12.5).

        Every ``TransactionStatus`` value is represented (count ``0`` when
        absent) and the group counts sum exactly to ``total`` (Property 36).
        """
        stmt = select(Transaction.status).select_from(Transaction)
        statuses = (await self.db.execute(stmt)).scalars().all()

        counts: dict[str, int] = {status.value: 0 for status in TransactionStatus}
        for status_value in statuses:
            # Tolerate any unexpected status value by surfacing it as its own group.
            counts[status_value] = counts.get(status_value, 0) + 1

        groups = [
            TransactionStatusCount(status=status_value, count=count)
            for status_value, count in counts.items()
        ]
        total = sum(count for _, count in counts.items())
        return TransactionBreakdown(total=total, groups=groups)

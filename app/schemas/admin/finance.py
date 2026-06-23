"""Request/response schemas for the financial dashboard (Requirement 12).

The Finance_Service exposes three read endpoints, all gated by ``finance.read``:

* ``GET /admin/finance/dashboard/kpis?range=`` returns a flat list of
  :class:`FinanceKPI` records — each ``{id, title, type, value}`` — aggregated
  over the window a ``Time_Range`` resolves to (R12.1, R12.2). Every monetary
  metric is an INR amount (R12.2).
* ``GET /admin/finance/dashboard/series?range=`` returns a
  :class:`FinanceSeries` whose ``series`` is an ordered list of ``{name, value}``
  points covering the window (R12.3).
* ``GET /admin/finance/transactions/breakdown`` returns a
  :class:`TransactionBreakdown` — transaction counts grouped by
  ``Transaction.status`` whose group counts sum to ``total`` (R12.5).

An invalid/missing ``Time_Range`` produces HTTP 422 (R12.4) — handled in the
router via :class:`~app.services.admin.time_range.InvalidTimeRangeError`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

#: The Metric_Type discriminator carried by every finance KPI (R12.2).
#: ``currency`` values are INR amounts.
MetricType = Literal["currency", "number", "percentage"]


class FinanceKPI(BaseModel):
    """A single financial KPI aggregated over a Time_Range (R12.1, R12.2).

    ``value`` is the aggregated current-period figure; for ``currency`` KPIs it
    is an INR amount (R12.2).
    """

    id: str
    title: str
    type: MetricType
    value: float


class FinanceSeriesPoint(BaseModel):
    """One ordered point in a financial time-series (R12.3).

    ``name`` is the bucket label (mirroring the UI's ``generateChartData``
    labels) and ``value`` is the metric aggregated over that bucket.
    """

    name: str
    value: float


class FinanceSeries(BaseModel):
    """An ordered financial time-series over a Time_Range (R12.3).

    ``series`` covers the whole window in chronological order. ``id``/``title``/
    ``type`` identify which financial metric the series describes (revenue by
    default); for ``currency`` metrics the point values are INR amounts.
    """

    id: str
    title: str
    type: MetricType
    range: str
    series: list[FinanceSeriesPoint]


class TransactionStatusCount(BaseModel):
    """The transaction count for a single ``Transaction.status`` group (R12.5)."""

    status: str
    count: int


class TransactionBreakdown(BaseModel):
    """Transaction counts grouped by status (R12.5).

    ``groups`` carries one entry per ``Transaction.status`` value and the group
    counts sum exactly to ``total`` (design Property 36).
    """

    total: int
    groups: list[TransactionStatusCount]

"""Request/response schemas for the analytics views (Requirement 18).

The Analytics_Service exposes four read endpoints, all gated by
``analytics.read``, each aggregating *real* backend rows over the window a
``Time_Range`` resolves to:

* ``GET /admin/analytics/user-activity?range=`` — user engagement metrics (R18.1).
* ``GET /admin/analytics/merchant-activity?range=`` — merchant engagement metrics
  (R18.2).
* ``GET /admin/analytics/wallet-referral?range=`` — wallet recharge / spend /
  referral-conversion metrics (R18.3).
* ``GET /admin/analytics/ai-data-usage?range=`` — AI generation / failure /
  data-usage metrics (R18.4).

Every view returns a flat list of :class:`AnalyticsMetric` records — each
``{id, title, type, value[, note]}``. ``currency`` values are INR amounts (every
money column in the schema is INR-denominated). Metrics that represent a *rate or
average* are returned as an average over the window rather than a sum (R18.6).

An invalid/missing ``Time_Range`` produces HTTP 422 (R18.5) — handled in the
router via :class:`~app.services.admin.time_range.InvalidTimeRangeError`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

#: The Metric_Type discriminator carried by every analytics metric.
#: ``currency`` values are INR amounts; ``percentage`` values are rates averaged
#: over the window (R18.6).
MetricType = Literal["currency", "number", "percentage"]


class AnalyticsMetric(BaseModel):
    """A single analytics metric aggregated over a Time_Range.

    ``value`` is the aggregated current-period figure. Sum-type metrics are
    summed over the window; rate/average/percentage metrics are averaged over
    the window rather than summed (R18.6). For ``currency`` metrics ``value`` is
    an INR amount. ``note`` documents any approximated data source.
    """

    id: str
    title: str
    type: MetricType
    value: float
    note: str | None = None


class AnalyticsView(BaseModel):
    """A named analytics view — a list of metrics over a Time_Range.

    ``view`` identifies the analytics surface (``user-activity``,
    ``merchant-activity``, ``wallet-referral`` or ``ai-data-usage``); ``range``
    echoes the resolved Time_Range; ``metrics`` carries the aggregated figures.
    """

    view: str
    range: str
    metrics: list[AnalyticsMetric]

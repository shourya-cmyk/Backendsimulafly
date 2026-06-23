"""Request/response schemas for the executive dashboard (Requirement 5).

The executive dashboard exposes two read endpoints, both gated by
``dashboard.read`` and parameterised by a ``Time_Range``:

* ``GET /admin/dashboard/executive/kpis`` returns a flat list of
  :class:`KPIValue` records — each ``{id, title, type, value[, prior]}`` —
  aggregated over the resolved window (R5.1–R5.3, R5.7).
* ``GET /admin/dashboard/executive/kpis/{id}/series`` returns an ordered
  :class:`KPISeries` whose ``series`` is a list of ``{name, value}`` points
  covering the window (R5.4, R5.5).

``type`` is one of ``currency`` (INR amount), ``number``, or ``percentage``
(R5.3). Sum-type KPIs are summed over the window; rate/average/percentage KPIs
are averaged (R5.5) — see :mod:`app.services.admin.dashboard_service`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

#: The Metric_Type discriminator carried by every KPI (R5.2, R5.3).
MetricType = Literal["currency", "number", "percentage"]


class KPIValue(BaseModel):
    """A single executive KPI aggregated over a Time_Range (R5.1–R5.3, R5.7).

    ``value`` is the aggregated current-period figure; for ``currency`` KPIs it
    is an INR amount (R5.3). ``prior`` carries the prior-period value and is
    populated only when the request sets ``compare=true`` (R5.7); otherwise it
    is ``None`` and omitted from trend rendering.
    """

    id: str
    title: str
    type: MetricType
    value: float
    prior: float | None = None


class KPISeriesPoint(BaseModel):
    """One ordered point in a KPI time-series (R5.4).

    ``name`` is the bucket label (mirroring the UI's ``generateChartData``
    labels) and ``value`` is the metric aggregated over that bucket.
    """

    name: str
    value: float


class KPISeries(BaseModel):
    """An ordered time-series for a single KPI over a Time_Range (R5.4, R5.5).

    ``series`` covers the whole window in chronological order; sum-type KPIs are
    summed per bucket while rate/average/percentage KPIs are averaged per bucket
    (R5.5).
    """

    id: str
    title: str
    type: MetricType
    range: str
    series: list[KPISeriesPoint]

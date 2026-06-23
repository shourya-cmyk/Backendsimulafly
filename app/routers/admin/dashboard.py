"""Admin executive dashboard router (Requirement 5).

Endpoints (all prefixed ``/admin``; mounted under ``/api/v1`` by the wiring
step), both gated by ``require_permission("dashboard.read")``:

| Method | Path                                          | Req         |
|--------|-----------------------------------------------|-------------|
| GET    | `/dashboard/executive/kpis`                   | 5.1–5.3,5.7 |
| GET    | `/dashboard/executive/kpis/{id}/series`       | 5.4, 5.5    |
| GET    | `/dashboard/executive/overview`               | snapshot    |
| GET    | `/dashboard/executive/activity`               | feed        |

The ``range`` query param selects a Time_Range; an invalid/missing value raises
:class:`~app.services.admin.time_range.InvalidTimeRangeError`, which is
translated here to HTTP 422 (R5.6). The ``compare`` flag on the KPI list adds a
prior-period value per KPI (R5.7). A series request for an unknown KPI id
returns HTTP 404. All routes are read-only, so no audit entry is written.

The ``overview`` route returns real aggregate counts powering the Executive
Dashboard's SnapshotWidgets + funnel, and ``activity`` returns a lightweight
recent-audit-log list backing the Realtime Feed; both require ``dashboard.read``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.admin.dashboard import KPISeries, KPIValue
from app.schemas.admin.overview import ActivityEntry, DashboardOverview
from app.services.admin.dashboard_overview_service import (
    DEFAULT_ACTIVITY_LIMIT,
    DashboardOverviewService,
)
from app.services.admin.dashboard_service import DashboardService
from app.services.admin.time_range import InvalidTimeRangeError
from app.utils.admin_dependencies import require_permission

router = APIRouter(prefix="/admin", tags=["admin-dashboard"])

DBSession = Annotated[AsyncSession, Depends(get_db)]


def _resolve_or_422(exc: InvalidTimeRangeError) -> HTTPException:
    """Translate an invalid Time_Range into an HTTP 422 (R5.6)."""
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


@router.get(
    "/dashboard/executive/kpis",
    response_model=list[KPIValue],
    dependencies=[Depends(require_permission("dashboard.read"))],
)
async def executive_kpis(
    db: DBSession,
    range: Annotated[str | None, Query(description="Time_Range to aggregate over")] = None,
    compare: Annotated[bool, Query(description="Include prior-period values")] = False,
) -> list[KPIValue]:
    """Executive KPIs aggregated over a Time_Range (R5.1–R5.3); ``compare`` adds prior (R5.7)."""
    try:
        return await DashboardService(db).executive_kpis(range, compare=compare)
    except InvalidTimeRangeError as exc:
        raise _resolve_or_422(exc)


@router.get(
    "/dashboard/executive/kpis/{kpi_id}/series",
    response_model=KPISeries,
    dependencies=[Depends(require_permission("dashboard.read"))],
)
async def executive_kpi_series(
    kpi_id: str,
    db: DBSession,
    range: Annotated[str | None, Query(description="Time_Range to aggregate over")] = None,
) -> KPISeries:
    """Ordered time-series for one KPI over a Time_Range (R5.4, R5.5); 404 if unknown."""
    try:
        series = await DashboardService(db).kpi_series(kpi_id, range)
    except InvalidTimeRangeError as exc:
        raise _resolve_or_422(exc)
    if series is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="unknown KPI id"
        )
    return series


@router.get(
    "/dashboard/executive/overview",
    response_model=DashboardOverview,
    dependencies=[Depends(require_permission("dashboard.read"))],
)
async def executive_overview(db: DBSession) -> DashboardOverview:
    """Real aggregate counts powering the SnapshotWidgets + funnel (read-only)."""
    return await DashboardOverviewService(db).overview()


@router.get(
    "/dashboard/executive/activity",
    response_model=list[ActivityEntry],
    dependencies=[Depends(require_permission("dashboard.read"))],
)
async def executive_activity(
    db: DBSession,
    limit: Annotated[
        int,
        Query(ge=1, le=100, description="Number of recent audit entries to return"),
    ] = DEFAULT_ACTIVITY_LIMIT,
) -> list[ActivityEntry]:
    """Most recent audit-log entries backing the Realtime Feed (read-only)."""
    return await DashboardOverviewService(db).activity(limit=limit)

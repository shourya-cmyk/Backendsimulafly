"""Admin financial dashboard router (Requirement 12).

Endpoints (all prefixed ``/admin``; mounted under ``/api/v1`` by the wiring
step), all gated by ``require_permission("finance.read")``:

| Method | Path                              | Req         |
|--------|-----------------------------------|-------------|
| GET    | `/finance/dashboard/kpis`         | 12.1–12.3   |
| GET    | `/finance/dashboard/series`       | 12.3        |
| GET    | `/finance/transactions/breakdown` | 12.5        |

The ``range`` query param selects a Time_Range; an invalid/missing value raises
:class:`~app.services.admin.time_range.InvalidTimeRangeError`, translated here
to HTTP 422 (R12.4). Monetary metrics are INR amounts (R12.2). All routes are
read-only, so no audit entry is written.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.admin.finance import (
    FinanceKPI,
    FinanceSeries,
    TransactionBreakdown,
)
from app.services.admin.finance_service import FinanceService
from app.services.admin.time_range import InvalidTimeRangeError
from app.utils.admin_dependencies import require_permission

router = APIRouter(prefix="/admin", tags=["admin-finance"])

DBSession = Annotated[AsyncSession, Depends(get_db)]


def _invalid_range_422(exc: InvalidTimeRangeError) -> HTTPException:
    """Translate an invalid Time_Range into an HTTP 422 (R12.4)."""
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


@router.get(
    "/finance/dashboard/kpis",
    response_model=list[FinanceKPI],
    dependencies=[Depends(require_permission("finance.read"))],
)
async def finance_kpis(
    db: DBSession,
    range: Annotated[str | None, Query(description="Time_Range to aggregate over")] = None,
) -> list[FinanceKPI]:
    """Financial KPIs (revenue, transaction volume, wallet balance) over a Time_Range (R12.1, R12.2)."""
    try:
        return await FinanceService(db).finance_kpis(range)
    except InvalidTimeRangeError as exc:
        raise _invalid_range_422(exc)


@router.get(
    "/finance/dashboard/series",
    response_model=FinanceSeries,
    dependencies=[Depends(require_permission("finance.read"))],
)
async def finance_series(
    db: DBSession,
    range: Annotated[str | None, Query(description="Time_Range to aggregate over")] = None,
) -> FinanceSeries:
    """Ordered financial time-series (revenue) over a Time_Range (R12.3)."""
    try:
        series = await FinanceService(db).finance_series(range)
    except InvalidTimeRangeError as exc:
        raise _invalid_range_422(exc)
    if series is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="unknown finance metric id"
        )
    return series


@router.get(
    "/finance/transactions/breakdown",
    response_model=TransactionBreakdown,
    dependencies=[Depends(require_permission("finance.read"))],
)
async def transactions_breakdown(db: DBSession) -> TransactionBreakdown:
    """Transaction counts grouped by transaction status (R12.5)."""
    return await FinanceService(db).transactions_breakdown()

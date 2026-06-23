"""Admin analytics router (Requirement 18).

Endpoints (all prefixed ``/admin``; mounted under ``/api/v1`` by the wiring
step), all gated by ``require_permission("analytics.read")``:

| Method | Path                            | Req   |
|--------|---------------------------------|-------|
| GET    | `/analytics/user-activity`      | 18.1  |
| GET    | `/analytics/merchant-activity`  | 18.2  |
| GET    | `/analytics/wallet-referral`    | 18.3  |
| GET    | `/analytics/ai-data-usage`      | 18.4  |

The ``range`` query param selects a Time_Range; an invalid/missing value raises
:class:`~app.services.admin.time_range.InvalidTimeRangeError`, translated here to
HTTP 422 (R18.5). Metrics that represent a rate or average are returned as an
average over the window rather than a sum (R18.6); ``currency`` metrics are INR
amounts. All routes are read-only, so no audit entry is written.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.admin.analytics import AnalyticsView
from app.services.admin.analytics_service import AnalyticsService
from app.services.admin.time_range import InvalidTimeRangeError
from app.utils.admin_dependencies import require_permission

router = APIRouter(prefix="/admin", tags=["admin-analytics"])

DBSession = Annotated[AsyncSession, Depends(get_db)]
RangeQuery = Annotated[str | None, Query(description="Time_Range to aggregate over")]


def _invalid_range_422(exc: InvalidTimeRangeError) -> HTTPException:
    """Translate an invalid Time_Range into an HTTP 422 (R18.5)."""
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


async def _view(db: AsyncSession, view_name: str, range_str: str | None) -> AnalyticsView:
    """Aggregate ``view_name`` over ``range_str``, mapping bad ranges to 422."""
    try:
        return await AnalyticsService(db).view(view_name, range_str)
    except InvalidTimeRangeError as exc:
        raise _invalid_range_422(exc)


@router.get(
    "/analytics/user-activity",
    response_model=AnalyticsView,
    dependencies=[Depends(require_permission("analytics.read"))],
)
async def user_activity(db: DBSession, range: RangeQuery = None) -> AnalyticsView:
    """User engagement metrics aggregated over a Time_Range (R18.1)."""
    return await _view(db, "user-activity", range)


@router.get(
    "/analytics/merchant-activity",
    response_model=AnalyticsView,
    dependencies=[Depends(require_permission("analytics.read"))],
)
async def merchant_activity(db: DBSession, range: RangeQuery = None) -> AnalyticsView:
    """Merchant engagement metrics aggregated over a Time_Range (R18.2)."""
    return await _view(db, "merchant-activity", range)


@router.get(
    "/analytics/wallet-referral",
    response_model=AnalyticsView,
    dependencies=[Depends(require_permission("analytics.read"))],
)
async def wallet_referral(db: DBSession, range: RangeQuery = None) -> AnalyticsView:
    """Wallet recharge / spend / referral-conversion metrics over a Time_Range (R18.3)."""
    return await _view(db, "wallet-referral", range)


@router.get(
    "/analytics/ai-data-usage",
    response_model=AnalyticsView,
    dependencies=[Depends(require_permission("analytics.read"))],
)
async def ai_data_usage(db: DBSession, range: RangeQuery = None) -> AnalyticsView:
    """AI generation / failure / data-usage metrics over a Time_Range (R18.4)."""
    return await _view(db, "ai-data-usage", range)

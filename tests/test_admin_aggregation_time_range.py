"""Regression tests for admin aggregation window-bound datetime binding.

The admin dashboard / finance / analytics services build window bounds as
tz-aware UTC datetimes (via ``resolve_time_range``) but several domain tables
declare ``created_at`` as a naive ``TIMESTAMP WITHOUT TIME ZONE``. asyncpg
cannot bind a tz-aware datetime to a naive timestamp parameter, so the services
now coerce the window bounds to naive UTC right before binding them into SQL.

These tests exercise every analytics view plus the finance/dashboard KPI and
series surfaces over a valid ``Time_Range`` against the SQLite test engine and
assert they run without a datetime binding error and aggregate seeded rows
correctly (the window/bucketing stays consistent after the coercion).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.models.wallet import Transaction, TransactionStatus
from app.services.admin.analytics_service import ANALYTICS_VIEWS, AnalyticsService
from app.services.admin.dashboard_service import KPI_REGISTRY, DashboardService
from app.services.admin.finance_service import FinanceService

# A wide range so seeded rows created "moments ago" fall inside the window.
_RANGE = "24 Hours"


def _window_end() -> datetime:
    """A tz-aware "now" just ahead of real time so freshly-seeded rows (created
    via ``func.now()``) fall strictly before ``window_end``."""
    return datetime.now(timezone.utc) + timedelta(minutes=1)


@pytest.mark.asyncio
async def test_analytics_views_run_over_valid_range(db_session, test_user):
    """Every analytics view aggregates over a valid range without erroring."""
    now = _window_end()
    service = AnalyticsService(db_session)
    for view_name in ANALYTICS_VIEWS:
        result = await service.view(view_name, _RANGE, now=now)
        assert result.view == view_name
        assert result.range == _RANGE
        # Each view exposes its full metric set.
        assert len(result.metrics) == len(ANALYTICS_VIEWS[view_name])


@pytest.mark.asyncio
async def test_finance_kpis_and_series_run_over_valid_range(db_session):
    """Finance KPIs/series run over a valid range and count a seeded txn."""
    now = _window_end()
    # Seed one successful transaction inside the window (FKs are not enforced
    # on the test SQLite engine, so a bare merchant_id is fine). ``created_at``
    # is tz-aware UTC — the same shape the window bounds have.
    txn = Transaction(
        merchant_id=uuid.uuid4(),
        amount=Decimal("150.00"),
        status=TransactionStatus.SUCCESSFUL.value,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(txn)
    await db_session.commit()

    service = FinanceService(db_session)

    kpis = await service.finance_kpis(_RANGE, now=now)
    by_id = {k.id: k for k in kpis}
    # The seeded successful transaction is counted in revenue/volume.
    assert by_id["revenue"].value == pytest.approx(150.0)
    assert by_id["transaction_volume"].value == 1
    assert by_id["successful_transactions"].value == 1

    series = await service.finance_series(_RANGE, now=now)
    assert series is not None
    assert series.id == "revenue"
    # The bucketed revenue across the window sums to the seeded amount.
    assert sum(p.value for p in series.series) == pytest.approx(150.0)


@pytest.mark.asyncio
async def test_dashboard_kpis_and_series_run_over_valid_range(db_session, test_user):
    """Dashboard KPIs/series run over a valid range without a binding error."""
    now = _window_end()
    service = DashboardService(db_session)

    kpis = await service.executive_kpis(_RANGE, compare=True, now=now)
    assert len(kpis) == len(KPI_REGISTRY)
    by_id = {k.id: k for k in kpis}
    # ``test_user`` is created within the window.
    assert by_id["new_signups"].value >= 1
    # compare=True populates the prior-period value without erroring.
    assert by_id["new_signups"].prior is not None

    series = await service.kpi_series("new_signups", _RANGE, now=now)
    assert series is not None
    assert series.id == "new_signups"
    assert len(series.series) > 0

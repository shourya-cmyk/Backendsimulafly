"""Admin webhook/system monitoring router (Requirement 23).

Endpoints (all prefixed ``/admin``; mounted under ``/api/v1`` by the wiring
step):

| Method | Path                                | Permission       | Req         |
|--------|-------------------------------------|------------------|-------------|
| GET    | `/system/webhooks`                  | `system.read`    | 23.1        |
| POST   | `/system/webhooks/{id}/redeliver`   | `system.manage`  | 23.2        |
| GET    | `/system/health`                    | `system.read`    | 23.3, 23.5  |
| GET    | `/system/counters`                  | `system.read`    | 23.4        |

Reads are gated by ``system.read`` and redelivery by ``system.manage`` (403
when missing). The redelivery route is wrapped by ``audited(...)`` so each
re-enqueue writes one immutable audit entry capturing the acting admin and
affected delivery (R23.2).

The background scheduler is read defensively from ``request.app.state.scheduler``
(APScheduler is started in :func:`app.main.lifespan`). If it is absent — e.g.
under a test client that doesn't run the lifespan — the System_Health_Service
reports the scheduler as ``degraded`` rather than failing the request (R23.5),
mirroring the existing ``/readyz`` semantics.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.admin import AdminAccount
from app.schemas.admin.listing import ListingEnvelope
from app.schemas.admin.system import (
    DependencyHealth,
    OperationalCounters,
    SystemHealth,
    WebhookDeliveryOut,
)
from app.services.admin.audit_service import AuditContext, audited
from app.services.admin.health_service import (
    STATUS_DEGRADED,
    STATUS_OK,
    SystemHealthService,
)
from app.services.admin.listing import ListParams
from app.utils.admin_dependencies import get_current_admin, require_permission

router = APIRouter(prefix="/admin", tags=["admin-system"])

DBSession = Annotated[AsyncSession, Depends(get_db)]


def _list_params(
    page: int = Query(default=1, ge=1),
    page_size: int | None = Query(default=None, ge=1),
    sort: str | None = Query(default=None),
    status: str | None = Query(default=None),
    event_type: str | None = Query(default=None),
) -> ListParams:
    """Translate query params into :class:`ListParams` for the listing engine.

    Webhook listings support pagination, an optional whitelisted ``sort``, and
    optional equality filters on ``status`` and ``event_type``.
    """
    filters: dict[str, object] = {}
    if status is not None:
        filters["status"] = status
    if event_type is not None:
        filters["event_type"] = event_type
    kwargs: dict[str, object] = {"page": page, "sort": sort, "filters": filters}
    if page_size is not None:
        kwargs["page_size"] = page_size
    return ListParams(**kwargs)


ListParamsDep = Annotated[ListParams, Depends(_list_params)]


def _get_scheduler(request: Request):
    """Read the APScheduler instance from app state, or ``None`` if absent."""
    return getattr(request.app.state, "scheduler", None)


@router.get(
    "/system/webhooks",
    response_model=ListingEnvelope[WebhookDeliveryOut],
    dependencies=[Depends(require_permission("system.read"))],
)
async def list_webhooks(
    db: DBSession,
    params: ListParamsDep,
) -> ListingEnvelope[WebhookDeliveryOut]:
    """Paginated webhook delivery log (R23.1)."""
    page = await SystemHealthService(db).list_webhooks(params)
    items = [WebhookDeliveryOut.model_validate(record) for record in page.items]
    return ListingEnvelope[WebhookDeliveryOut](
        items=items,
        page=page.page,
        page_size=page.page_size,
        total=page.total,
        total_pages=page.total_pages,
        has_next=page.has_next,
        next_page=page.next_page,
    )


@router.post(
    "/system/webhooks/{delivery_id}/redeliver",
    response_model=WebhookDeliveryOut,
    dependencies=[Depends(require_permission("system.manage"))],
)
async def redeliver_webhook(
    delivery_id: uuid.UUID,
    db: DBSession,
    actor: Annotated[AdminAccount, Depends(get_current_admin)],
    audit: Annotated[
        AuditContext, Depends(audited("system.webhooks.redeliver", "webhook_delivery"))
    ],
) -> WebhookDeliveryOut:
    """Re-enqueue a failed webhook delivery; 404/409 per service; audited (R23.2)."""
    record = await SystemHealthService(db).redeliver(delivery_id)
    audit.set_target(delivery_id)
    audit.add_metadata(event_type=record.event_type, attempt_count=record.attempt_count)
    return WebhookDeliveryOut.model_validate(record)


@router.get(
    "/system/health",
    response_model=SystemHealth,
    dependencies=[Depends(require_permission("system.read"))],
)
async def system_health(request: Request, db: DBSession) -> SystemHealth:
    """Database + background scheduler health; degraded on failure (R23.3, R23.5)."""
    scheduler = _get_scheduler(request)
    db_status, sched = await SystemHealthService(db).health(scheduler)
    overall = (
        STATUS_OK
        if db_status == STATUS_OK and sched["status"] == STATUS_OK
        else STATUS_DEGRADED
    )
    return SystemHealth(
        status=overall,
        database=DependencyHealth(status=db_status),
        scheduler=DependencyHealth(status=sched["status"], detail=sched["detail"]),
    )


@router.get(
    "/system/counters",
    response_model=OperationalCounters,
    dependencies=[Depends(require_permission("system.read"))],
)
async def system_counters(request: Request, db: DBSession) -> OperationalCounters:
    """Pending background jobs + recent processing failures (R23.4)."""
    scheduler = _get_scheduler(request)
    pending_jobs, recent_failures = await SystemHealthService(db).counters(scheduler)
    return OperationalCounters(
        pending_jobs=pending_jobs,
        recent_failures=recent_failures,
    )

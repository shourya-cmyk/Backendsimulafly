"""Admin Alert Center router (Requirement 6).

Endpoints (all prefixed ``/admin``; mounted under ``/api/v1`` by the wiring
step):

| Method | Path                                          | Permission       | Req        |
|--------|-----------------------------------------------|------------------|------------|
| GET    | `/alerts/counters`                            | `alerts.read`    | 6.1,6.2,6.10|
| GET    | `/alerts/{category}/items`                    | `alerts.read`    | 6.3, 6.4   |
| POST   | `/alerts/{category}/items/{id}/resolve`       | `alerts.resolve` | 6.5–6.9    |

The ``{category}`` path parameter is typed as
:class:`~app.schemas.admin.alerts.AlertCategory`; FastAPI rejects any value
outside the five-category whitelist with HTTP 422 (R6.4). Reads are gated by
``alerts.read`` and resolution by ``alerts.resolve`` (R6.9 → 403 when missing).
The resolve route is wrapped by ``audited(...)`` so each resolution writes one
immutable audit entry capturing the acting admin and affected record (R6.8).
:class:`~app.services.admin.alert_service.AlertService` raises HTTP 404 for a
missing item (R6.6) and HTTP 409 for an already-resolved item (R6.7).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.admin.alerts import (
    AlertCategory,
    AlertCounters,
    AlertItem,
    AlertResolution,
)
from app.schemas.admin.listing import ListingEnvelope
from app.models.admin import AdminAccount
from app.services.admin.alert_service import AlertService
from app.services.admin.audit_service import AuditContext, audited
from app.services.admin.listing import ListParams
from app.utils.admin_dependencies import get_current_admin, require_permission

router = APIRouter(prefix="/admin", tags=["admin-alerts"])

DBSession = Annotated[AsyncSession, Depends(get_db)]


def _list_params(
    page: int = Query(default=1, ge=1),
    page_size: int | None = Query(default=None, ge=1),
    sort: str | None = Query(default=None),
) -> ListParams:
    """Translate query params into :class:`ListParams` for the listing engine.

    Alert item listings carry no free-text search or filters; only pagination
    and an optional whitelisted ``sort`` (``created_at`` / ``-created_at``).
    """
    kwargs: dict[str, object] = {"page": page, "sort": sort}
    if page_size is not None:
        kwargs["page_size"] = page_size
    return ListParams(**kwargs)


ListParamsDep = Annotated[ListParams, Depends(_list_params)]


@router.get(
    "/alerts/counters",
    response_model=AlertCounters,
    response_model_by_alias=True,
    dependencies=[Depends(require_permission("alerts.read"))],
)
async def alert_counters(db: DBSession) -> AlertCounters:
    """Five non-negative alert counters computed from live sources (R6.1, R6.2, R6.10)."""
    return await AlertService(db).counters()


@router.get(
    "/alerts/{category}/items",
    response_model=ListingEnvelope[AlertItem],
    dependencies=[Depends(require_permission("alerts.read"))],
)
async def alert_items(
    category: AlertCategory,
    db: DBSession,
    params: ListParamsDep,
) -> ListingEnvelope[AlertItem]:
    """Paginated underlying records contributing to ``category`` (R6.3); 422 if unknown (R6.4)."""
    service = AlertService(db)
    page = await service.list_items(category, params)
    items = [service.to_item(category, record) for record in page.items]
    return ListingEnvelope[AlertItem](
        items=items,
        page=page.page,
        page_size=page.page_size,
        total=page.total,
        total_pages=page.total_pages,
        has_next=page.has_next,
        next_page=page.next_page,
    )


@router.post(
    "/alerts/{category}/items/{item_id}/resolve",
    response_model=AlertResolution,
    dependencies=[Depends(require_permission("alerts.resolve"))],
)
async def resolve_alert_item(
    category: AlertCategory,
    item_id: uuid.UUID,
    db: DBSession,
    actor: Annotated[AdminAccount, Depends(get_current_admin)],
    audit: Annotated[AuditContext, Depends(audited("alerts.resolve", "alert_item"))],
) -> AlertResolution:
    """Resolve an alert item so it no longer counts; 404/409/403 per R6.6–R6.9; audited (R6.8)."""
    await AlertService(db).resolve(category, item_id, actor_id=actor.id)
    audit.set_target(item_id)
    audit.add_metadata(category=category.value)
    return AlertResolution(id=str(item_id), category=category)

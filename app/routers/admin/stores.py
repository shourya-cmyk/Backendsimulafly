"""Admin stores router — store directory listing/detail/status (R9).

Endpoints (all prefixed ``/admin``; mounted under ``/api/v1`` by the wiring
step):

| Method | Path                   | Permission      | Req            |
|--------|------------------------|-----------------|----------------|
| GET    | `/stores`              | `stores.read`   | 9.1, 9.2, 9.3  |
| GET    | `/stores/{id}`         | `stores.read`   | 9.4, 9.6       |
| PATCH  | `/stores/{id}/status`  | `stores.manage` | 9.5            |

Reads are gated by ``require_permission("stores.read")`` and the status change
by ``require_permission("stores.manage")``. The mutating route is wrapped with
``audited(...)`` so each status change writes one immutable audit entry
(Requirement 9.5 / 19.1).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.store import Store
from app.schemas.admin.stores import (
    StoreDetail,
    StoreListItem,
    StoreListResponse,
    StoreStatusResponse,
    StoreStatusUpdateRequest,
)
from app.services.admin.audit_service import AuditContext, audited
from app.services.admin.listing import ListParams
from app.services.admin.store_service import StoreService
from app.utils.admin_dependencies import require_permission

router = APIRouter(prefix="/admin", tags=["admin-stores"])

DBSession = Annotated[AsyncSession, Depends(get_db)]


def _to_list_item(store: Store) -> StoreListItem:
    return StoreListItem(
        id=store.id,
        name=store.name,
        merchant_id=store.merchant_id,
        merchant_name=store.merchant.display_name if store.merchant else None,
        status=store.status,
        created_at=store.created_at,
    )


def _to_detail(store: Store) -> StoreDetail:
    return StoreDetail(
        id=store.id,
        name=store.name,
        merchant_id=store.merchant_id,
        merchant_name=store.merchant.display_name if store.merchant else None,
        status=store.status,
        created_at=store.created_at,
        settings=store.settings,
        updated_at=store.updated_at,
    )


def _to_status_response(store: Store) -> StoreStatusResponse:
    return StoreStatusResponse(
        id=store.id,
        name=store.name,
        merchant_id=store.merchant_id,
        status=store.status,
    )


@router.get(
    "/stores",
    response_model=StoreListResponse,
    dependencies=[Depends(require_permission("stores.read"))],
)
async def list_stores(
    db: DBSession,
    search: str | None = Query(default=None),
    sort: str | None = Query(default=None),
    merchant_id: uuid.UUID | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int | None = Query(default=None, ge=1),
) -> StoreListResponse:
    """Paginated store directory: id, name, owning merchant, status, created date.

    Searchable by name (R9.2) and filterable by owning merchant (R9.3); an
    unsupported sort field is rejected with HTTP 422.
    """
    params = ListParams(page=page, search=search, sort=sort)
    if page_size is not None:
        params.page_size = page_size

    page_obj = await StoreService(db).list_stores(params, merchant_id=merchant_id)
    return StoreListResponse(
        items=[_to_list_item(store) for store in page_obj.items],
        page=page_obj.page,
        page_size=page_obj.page_size,
        total=page_obj.total,
        total_pages=page_obj.total_pages,
        has_next=page_obj.has_next,
        next_page=page_obj.next_page,
    )


@router.get(
    "/stores/{store_id}",
    response_model=StoreDetail,
    dependencies=[Depends(require_permission("stores.read"))],
)
async def get_store(
    store_id: uuid.UUID,
    db: DBSession,
) -> StoreDetail:
    """Return a single store's detail record; 404 if missing (R9.4, R9.6)."""
    store = await StoreService(db).get_store(store_id)
    return _to_detail(store)


@router.patch(
    "/stores/{store_id}/status",
    response_model=StoreStatusResponse,
    dependencies=[Depends(require_permission("stores.manage"))],
)
async def update_store_status(
    store_id: uuid.UUID,
    payload: StoreStatusUpdateRequest,
    db: DBSession,
    audit: Annotated[AuditContext, Depends(audited("stores.status", "store"))],
) -> StoreStatusResponse:
    """Persist a new store status and record the action (R9.5)."""
    store = await StoreService(db).change_status(store_id, payload.status)
    audit.set_target(store_id)
    audit.add_metadata(status=store.status)
    return _to_status_response(store)

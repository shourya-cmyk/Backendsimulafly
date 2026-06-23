"""Admin audit log router (Requirement 19).

Endpoint (prefixed ``/admin``; mounted under ``/api/v1`` by the wiring step):

| Method | Path      | Permission   | Req            |
|--------|-----------|--------------|----------------|
| GET    | `/audit`  | `audit.read` | 19.2, 19.3, 19.5 |

The audit log is **immutable and insert-only** (see
:class:`app.models.admin.AuditLog`). This router therefore registers **no**
``POST``/``PUT``/``PATCH``/``DELETE`` route for ``/audit``; FastAPI answers any
modification attempt on the path with HTTP 405 (method not allowed), satisfying
R19.4 without any explicit handler.

``GET /audit`` is gated by ``require_permission("audit.read")`` (R19.5 → 403 when
missing) and returns a paginated list ordered by ``created_at`` descending
(R19.2) via the shared listing engine. Optional conjunctive filters narrow the
result set (R19.3):

* ``actor_admin_id`` — the acting admin account (equality).
* ``action`` — the action key (equality).
* ``target_type`` / ``target_id`` — the targeted resource (equality).
* ``date_from`` / ``date_to`` — an inclusive range over ``created_at``.

``AuditLog`` has no soft-delete column, so ``paginate`` is called without
``soft_delete_col``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.admin import AuditLog
from app.schemas.admin.audit import AuditEntryOut
from app.schemas.admin.listing import ListingEnvelope
from app.services.admin.listing import ListParams, paginate
from app.utils.admin_dependencies import require_permission

router = APIRouter(prefix="/admin", tags=["admin-audit"])

DBSession = Annotated[AsyncSession, Depends(get_db)]

# Sortable whitelist: audit entries are ordered by creation time only. Also used
# by the listing engine to validate the (defaulted) sort field.
_SORTABLE = {"created_at": AuditLog.created_at}


@router.get(
    "/audit",
    response_model=ListingEnvelope[AuditEntryOut],
    dependencies=[Depends(require_permission("audit.read"))],
)
async def list_audit_entries(
    db: DBSession,
    page: int = Query(default=1, ge=1),
    page_size: int | None = Query(default=None, ge=1),
    actor_admin_id: uuid.UUID | None = Query(default=None),
    action: str | None = Query(default=None),
    target_type: str | None = Query(default=None),
    target_id: str | None = Query(default=None),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
) -> ListingEnvelope[AuditEntryOut]:
    """Paginated audit entries ordered by ``created_at`` desc; filterable (R19.2, R19.3, R19.5)."""
    # Equality filters keyed by column expression so the engine applies them
    # directly; ``None`` values are ignored by ``apply_filters``.
    filters: dict[object, object] = {
        AuditLog.actor_admin_id: actor_admin_id,
        AuditLog.action: action,
        AuditLog.target_type: target_type,
        AuditLog.target_id: target_id,
    }
    kwargs: dict[str, object] = {
        "page": page,
        # Default ordering is most-recent-first (R19.2).
        "sort": "-created_at",
        "filters": filters,
    }
    if page_size is not None:
        kwargs["page_size"] = page_size
    params = ListParams(**kwargs)

    # Inclusive date-range predicate over created_at (R19.3) applied to the base
    # statement; the listing engine handles equality filters, search, and sort.
    base_stmt = select(AuditLog)
    if date_from is not None:
        base_stmt = base_stmt.where(AuditLog.created_at >= date_from)
    if date_to is not None:
        base_stmt = base_stmt.where(AuditLog.created_at <= date_to)

    result = await paginate(
        db,
        base_stmt,
        params=params,
        sortable=_SORTABLE,
        searchable=(),
    )
    items = [AuditEntryOut.model_validate(row) for row in result.items]
    return ListingEnvelope[AuditEntryOut](
        items=items,
        page=result.page,
        page_size=result.page_size,
        total=result.total,
        total_pages=result.total_pages,
        has_next=result.has_next,
        next_page=result.next_page,
    )

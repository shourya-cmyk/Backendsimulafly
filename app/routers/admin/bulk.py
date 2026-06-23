"""Admin soft-delete / restore / bulk-action router (Requirement 22).

Exposes generic endpoints that compose the model-agnostic primitives in
:mod:`app.services.admin.bulk` (``soft_delete``, ``restore``, ``bulk_apply``)
over any *soft-deletable* resource — that is, a resource whose model carries a
nullable ``deleted_at`` column. Each such resource is declared once in
:data:`SOFT_DELETE_RESOURCES`, keyed by its URL slug.

| Method | Path                          | Permission            | Req            |
|--------|-------------------------------|-----------------------|----------------|
| DELETE | `/{resource}/{id}`            | resource manage perm  | 22.1           |
| POST   | `/{resource}/{id}/restore`    | resource manage perm  | 22.3           |
| POST   | `/{resource}/bulk`            | resource manage perm  | 22.4–22.6      |

Soft-delete stamps ``deleted_at`` while retaining the row (R22.1); the shared
listing engine then hides it from default listings (R22.2). Restore clears
``deleted_at`` (R22.3). Bulk applies a per-record action and returns one outcome
per id (R22.4), recording one audit entry per affected record (R22.5); a request
exceeding ``ADMIN_MAX_BULK_RECORDS`` is rejected with HTTP 422 (R22.6).

Only resources with a ``deleted_at`` column are registered here: **stores,
invoices, support-tickets, orders**. Resources without soft-delete (users,
merchants, products) are intentionally absent.

Access is gated by each resource's *manage* permission, resolved dynamically
from the registry (the permission depends on which resource was requested).
Auditing is performed via :class:`app.services.admin.audit_service.AuditService`
with the resource-specific action/target — equivalent to the ``audited(...)``
router dependency, but parameterised per resource. An unknown slug yields
HTTP 404; a missing permission yields HTTP 403.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.invoice import Invoice
from app.models.lead import Order
from app.models.store import Store
from app.models.support import SupportTicket
from app.services.admin.audit_service import AuditService
from app.services.admin.bulk import bulk_apply, restore, soft_delete
from app.services.admin.rbac_service import AdminRBACService, is_permission_satisfied
from app.utils.admin_dependencies import CurrentAdmin

router = APIRouter(prefix="/admin", tags=["admin-bulk"])

DBSession = Annotated[AsyncSession, Depends(get_db)]


@dataclass(frozen=True)
class SoftDeleteResource:
    """A soft-deletable resource and how to audit/authorize actions on it.

    Attributes:
        model: The mapped model class (must carry a nullable ``deleted_at``).
        manage_permission: Permission key gating soft-delete/restore/bulk.
        target_type: Audit ``target_type`` for entries recorded on this resource.
    """

    model: type
    manage_permission: str
    target_type: str


#: Registry of soft-deletable resources keyed by URL slug. Only models that
#: carry a ``deleted_at`` column appear here (R22.1/R22.3 require soft-delete).
SOFT_DELETE_RESOURCES: dict[str, SoftDeleteResource] = {
    "stores": SoftDeleteResource(
        model=Store,
        manage_permission="stores.manage",
        target_type="store",
    ),
    "invoices": SoftDeleteResource(
        model=Invoice,
        manage_permission="invoices.manage",
        target_type="invoice",
    ),
    "support-tickets": SoftDeleteResource(
        model=SupportTicket,
        manage_permission="support.respond",
        target_type="support_ticket",
    ),
    "orders": SoftDeleteResource(
        model=Order,
        manage_permission="bookings.manage",
        target_type="booking",
    ),
}


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class SoftDeleteResponse(BaseModel):
    """Outcome of a single soft-delete or restore action."""

    id: uuid.UUID
    deleted: bool
    deleted_at: datetime | None = None


class BulkActionRequest(BaseModel):
    """A bulk action over a list of record identifiers (R22.4).

    ``action`` selects the per-record operation; both supported actions require
    a soft-deletable model. The id list size is bounded by
    ``ADMIN_MAX_BULK_RECORDS`` (enforced by the bulk engine → 422, R22.6).
    """

    ids: list[uuid.UUID] = Field(default_factory=list)
    action: Literal["soft_delete", "restore"] = "soft_delete"


class BulkRecordOutcome(BaseModel):
    """Per-record outcome of a bulk action (``{id, ok, error?}``)."""

    id: uuid.UUID
    ok: bool
    error: str | None = None


class BulkActionResponse(BaseModel):
    """The per-record outcome array returned by a bulk action (R22.4)."""

    results: list[BulkRecordOutcome]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_resource(resource: str) -> SoftDeleteResource:
    """Look up a soft-deletable resource by slug, or raise HTTP 404."""
    entry = SOFT_DELETE_RESOURCES.get(resource)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown soft-deletable resource: {resource!r}",
        )
    return entry


async def _require_manage_permission(
    db: AsyncSession, admin, permission: str
) -> None:
    """Enforce the resource's manage permission.

    Mirrors :func:`app.utils.admin_dependencies.require_permission` but resolves
    the required permission dynamically from the registry. The wildcard ``*``
    (Super Admin) satisfies any requirement. Raises HTTP 403 otherwise.
    """
    effective = await AdminRBACService(db).load_effective_permissions(admin.id)
    if not is_permission_satisfied(effective, (permission,)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="insufficient permissions",
        )


async def _load_or_404(db: AsyncSession, model: type, record_id: uuid.UUID):
    """Load a record by id or raise HTTP 404."""
    instance = await db.get(model, record_id)
    if instance is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="record not found",
        )
    return instance


# ---------------------------------------------------------------------------
# Bulk action (declared before the parameterised routes for clarity)
# ---------------------------------------------------------------------------


@router.post("/{resource}/bulk", response_model=BulkActionResponse)
async def bulk_action(
    resource: str,
    payload: BulkActionRequest,
    db: DBSession,
    admin: CurrentAdmin,
) -> BulkActionResponse:
    """Apply a bulk soft-delete/restore over a list of ids (R22.4–R22.6).

    Returns one outcome per id (R22.4) and records one audit entry per affected
    record (R22.5). A list exceeding ``ADMIN_MAX_BULK_RECORDS`` is rejected with
    HTTP 422 (R22.6). An unknown resource slug yields HTTP 404; a missing manage
    permission yields HTTP 403.
    """
    entry = _resolve_resource(resource)
    await _require_manage_permission(db, admin, entry.manage_permission)

    primitive = soft_delete if payload.action == "soft_delete" else restore

    async def _action(instance) -> None:
        await primitive(db, instance)

    results = await bulk_apply(
        db,
        model=entry.model,
        ids=payload.ids,
        action=_action,
        actor=admin,
        action_name=f"{entry.target_type}.bulk_{payload.action}",
        target_type=entry.target_type,
    )
    # bulk_apply flushes mutations + one audit row per record; commit persists them.
    await db.commit()
    return BulkActionResponse(
        results=[
            BulkRecordOutcome(id=r.id, ok=r.ok, error=r.error) for r in results
        ]
    )


@router.delete("/{resource}/{record_id}", response_model=SoftDeleteResponse)
async def soft_delete_record(
    resource: str,
    record_id: uuid.UUID,
    db: DBSession,
    admin: CurrentAdmin,
) -> SoftDeleteResponse:
    """Soft-delete a single record (R22.1).

    Stamps ``deleted_at`` while retaining the row, records one audit entry, and
    returns the record's deletion state. An unknown resource slug yields
    HTTP 404; a missing record yields HTTP 404; a missing manage permission
    yields HTTP 403.
    """
    entry = _resolve_resource(resource)
    await _require_manage_permission(db, admin, entry.manage_permission)

    instance = await _load_or_404(db, entry.model, record_id)
    await soft_delete(db, instance)
    await AuditService(db).record(
        actor=admin,
        action=f"{entry.target_type}.soft_delete",
        target_type=entry.target_type,
        target_id=record_id,
    )
    await db.commit()
    return SoftDeleteResponse(
        id=record_id, deleted=True, deleted_at=instance.deleted_at
    )


@router.post("/{resource}/{record_id}/restore", response_model=SoftDeleteResponse)
async def restore_record(
    resource: str,
    record_id: uuid.UUID,
    db: DBSession,
    admin: CurrentAdmin,
) -> SoftDeleteResponse:
    """Restore a soft-deleted record (R22.3).

    Clears ``deleted_at`` (returning the record to active status), records one
    audit entry, and returns the record's deletion state. An unknown resource
    slug yields HTTP 404; a missing record yields HTTP 404; a missing manage
    permission yields HTTP 403.
    """
    entry = _resolve_resource(resource)
    await _require_manage_permission(db, admin, entry.manage_permission)

    instance = await _load_or_404(db, entry.model, record_id)
    await restore(db, instance)
    await AuditService(db).record(
        actor=admin,
        action=f"{entry.target_type}.restore",
        target_type=entry.target_type,
        target_id=record_id,
    )
    await db.commit()
    return SoftDeleteResponse(
        id=record_id, deleted=False, deleted_at=instance.deleted_at
    )

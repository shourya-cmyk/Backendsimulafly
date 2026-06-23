"""Soft-delete / restore primitives and the bulk-action engine (R22).

This module provides reusable, model-agnostic helpers that every admin feature
router can compose to satisfy Requirement 22 (Soft-Delete, Restore, and Bulk
Actions):

* :func:`soft_delete` — mark a record deleted by stamping its ``deleted_at``
  column with the current time while retaining the underlying row (R22.1). The
  companion listing engine (:func:`app.services.admin.listing.paginate`) then
  excludes the row from default listings (R22.2).
* :func:`restore` — clear ``deleted_at`` (set it to ``None``), returning the
  record to active status (R22.3).
* :func:`bulk_apply` — apply a caller-supplied per-record action over a list of
  identifiers, returning one outcome per identifier (R22.4) and emitting exactly
  one audit entry per affected record (R22.5). A request whose identifier list
  exceeds ``ADMIN_MAX_BULK_RECORDS`` is rejected with HTTP 422 (R22.6).

The helpers are intentionally generic over a SQLAlchemy model class that carries
a nullable ``deleted_at`` column. The bulk action is supplied as a callable so a
feature router can pass any per-record operation (suspend, delete, restore, …)
without this module knowing the concrete domain.
"""
from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.admin import AdminAccount
from app.services.admin.audit_service import (
    OUTCOME_FAILURE,
    OUTCOME_SUCCESS,
    AuditService,
)

#: Name of the soft-delete timestamp column expected on soft-deletable models.
DELETED_AT_ATTR = "deleted_at"

#: A per-record bulk action. Receives the loaded model instance and performs the
#: domain mutation (suspend/delete/restore/…). Any exception it raises is
#: captured and surfaced as a failure outcome for that record.
BulkAction = Callable[[Any], Awaitable[None]]


def _now() -> datetime:
    """Return the current UTC time (matches the convention used across admin services)."""
    return datetime.now(timezone.utc)


async def soft_delete(db: AsyncSession, instance: Any) -> Any:
    """Soft-delete ``instance`` by stamping its ``deleted_at`` with the current time (R22.1).

    The row is retained; only the deletion timestamp is set. The change is
    flushed so it participates in the surrounding request transaction (which
    owns the final commit). Returns the same instance for convenient chaining.

    Raises:
        AttributeError: if ``instance`` has no ``deleted_at`` attribute (i.e. the
            model does not support soft-delete).
    """
    if not hasattr(instance, DELETED_AT_ATTR):
        raise AttributeError(
            f"{type(instance).__name__} does not support soft-delete "
            f"(missing {DELETED_AT_ATTR!r} column)"
        )
    setattr(instance, DELETED_AT_ATTR, _now())
    await db.flush()
    return instance


async def restore(db: AsyncSession, instance: Any) -> Any:
    """Restore a soft-deleted ``instance`` by clearing its ``deleted_at`` (R22.3).

    Returns the record to active status by setting ``deleted_at`` to ``None``.
    The change is flushed into the surrounding transaction. Returns the same
    instance for convenient chaining.

    Raises:
        AttributeError: if ``instance`` has no ``deleted_at`` attribute.
    """
    if not hasattr(instance, DELETED_AT_ATTR):
        raise AttributeError(
            f"{type(instance).__name__} does not support soft-delete "
            f"(missing {DELETED_AT_ATTR!r} column)"
        )
    setattr(instance, DELETED_AT_ATTR, None)
    await db.flush()
    return instance


@dataclass
class BulkRecordResult:
    """Outcome of applying a bulk action to a single identifier.

    Serialises to ``{"id": ..., "ok": bool, "error": str | None}`` — the
    per-record outcome shape required by R22.4.
    """

    id: Any
    ok: bool
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Render the per-record outcome; ``error`` is omitted on success."""
        payload: dict[str, Any] = {"id": self.id, "ok": self.ok}
        if not self.ok:
            payload["error"] = self.error
        return payload


async def bulk_apply(
    db: AsyncSession,
    *,
    model: type[Any],
    ids: Sequence[Any],
    action: BulkAction,
    actor: AdminAccount | uuid.UUID | None,
    action_name: str,
    target_type: str,
) -> list[BulkRecordResult]:
    """Apply ``action`` to each identified record, returning per-record outcomes.

    For every id in ``ids`` the record is loaded via :meth:`AsyncSession.get`;
    ``action`` is then invoked with the loaded instance. A missing record or any
    exception raised by ``action`` is captured as a failure outcome rather than
    aborting the whole request — bulk operations are per-record (R22.4). Exactly
    one audit entry is recorded per affected record, capturing the success or
    failure outcome (R22.5).

    Args:
        db: Async SQLAlchemy session.
        model: The mapped model class to load each id against. Need not support
            soft-delete unless ``action`` performs a soft-delete/restore.
        ids: Record identifiers to act on. Must not exceed
            ``ADMIN_MAX_BULK_RECORDS`` (R22.6).
        action: Awaitable callable applied to each loaded instance.
        actor: The acting admin (or its id, or ``None`` for system actions);
            forwarded to :meth:`AuditService.record`.
        action_name: The audited action string (e.g. ``"merchants.suspend"``).
        target_type: The audited target type (e.g. ``"merchant"``).

    Returns:
        A list of :class:`BulkRecordResult`, one per input id, in input order.

    Raises:
        HTTPException: 422 when ``len(ids)`` exceeds ``ADMIN_MAX_BULK_RECORDS``
            (R22.6).
    """
    max_records = get_settings().ADMIN_MAX_BULK_RECORDS
    if len(ids) > max_records:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Bulk request exceeds the maximum of {max_records} records "
                f"per request (received {len(ids)})."
            ),
        )

    audit = AuditService(db)
    results: list[BulkRecordResult] = []

    for record_id in ids:
        error: str | None = None
        try:
            instance = await db.get(model, record_id)
            if instance is None:
                error = "not found"
            else:
                await action(instance)
                await db.flush()
        except Exception as exc:  # noqa: BLE001 - captured as a per-record failure
            error = str(exc) or exc.__class__.__name__

        ok = error is None
        # One audit entry per affected record (R22.5).
        await audit.record(
            actor=actor,
            action=action_name,
            target_type=target_type,
            target_id=record_id,
            outcome=OUTCOME_SUCCESS if ok else OUTCOME_FAILURE,
            metadata=None if ok else {"error": error},
        )
        results.append(BulkRecordResult(id=record_id, ok=ok, error=error))

    return results

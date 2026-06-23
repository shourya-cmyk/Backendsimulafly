"""Audit logging — immutable recording of admin actions (Requirement 19.1).

The Audit_Service appends insert-only :class:`AuditLog` rows describing *who*
did *what*, to *which* target, and with what *outcome*. Audit rows are never
updated or deleted (the audit table exposes no update/delete endpoints — see
design "Audit logging (R19)"); recording is therefore an append-only operation.

Two entry points are provided:

- :class:`AuditService.record` — the low-level async writer. Given an actor,
  action, target, and outcome it inserts a single ``AuditLog`` row and flushes
  it so the generated ``id``/``created_at`` are populated.
- :func:`audited` — a FastAPI dependency *factory* used at the router boundary.
  It yields an :class:`AuditContext` the handler mutates (setting the resolved
  ``target_id``, ``outcome``, and any ``metadata``); after the handler runs the
  dependency records the entry, capturing the current admin as the actor. If the
  handler raises, the entry is still recorded with a ``failure`` outcome so the
  attempt is not lost.

The actor is resolved via :func:`app.utils.admin_dependencies.get_current_admin`
so every mutating endpoint that declares an ``audited(...)`` dependency records
the acting :class:`AdminAccount` automatically.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Callable, Coroutine

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.admin import AdminAccount, AuditLog
from app.utils.admin_dependencies import get_current_admin

#: Conventional outcome strings stored in ``AuditLog.outcome``.
OUTCOME_SUCCESS = "success"
OUTCOME_FAILURE = "failure"


def _coerce_target_id(target_id: Any) -> str | None:
    """Normalize a target identifier to the ``String`` column's expected form.

    ``AuditLog.target_id`` is a nullable string so it can reference targets with
    heterogeneous key types (UUIDs, ints, slugs). ``None`` is preserved; any
    other value is stringified.
    """
    if target_id is None:
        return None
    return str(target_id)


class AuditService:
    """Append-only writer for :class:`AuditLog` rows."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def record(
        self,
        *,
        actor: AdminAccount | uuid.UUID | None,
        action: str,
        target_type: str,
        target_id: Any = None,
        outcome: str = OUTCOME_SUCCESS,
        metadata: dict | None = None,
    ) -> AuditLog:
        """Insert a single immutable audit entry and return it.

        ``actor`` accepts either a loaded :class:`AdminAccount`, a bare account
        ``UUID``, or ``None`` (for system-originated actions where no admin is
        attributable). ``target_id`` may be any value; it is stored as a string.
        The row is flushed so its server-generated ``id`` and ``created_at`` are
        available to the caller, but the surrounding request transaction owns
        the final commit.
        """
        if isinstance(actor, AdminAccount):
            actor_admin_id: uuid.UUID | None = actor.id
        else:
            actor_admin_id = actor

        entry = AuditLog(
            actor_admin_id=actor_admin_id,
            action=action,
            target_type=target_type,
            target_id=_coerce_target_id(target_id),
            outcome=outcome,
            audit_metadata=metadata or {},
        )
        self._db.add(entry)
        await self._db.flush()
        return entry


class AuditContext:
    """Mutable per-request handle a handler fills in before the entry is written.

    A handler that declares an :func:`audited` dependency receives one of these.
    It sets :attr:`target_id` (the resolved id of the affected record) and may
    override :attr:`outcome` or attach :attr:`metadata`. After the handler
    returns, the dependency reads this context and writes the audit row.

    Call :meth:`skip` to suppress recording (e.g. when a handler short-circuits
    before performing any mutation and recording would be misleading).
    """

    __slots__ = ("action", "target_type", "target_id", "outcome", "metadata", "_skip")

    def __init__(self, action: str, target_type: str) -> None:
        self.action = action
        self.target_type = target_type
        self.target_id: Any = None
        self.outcome: str = OUTCOME_SUCCESS
        self.metadata: dict = {}
        self._skip = False

    def set_target(self, target_id: Any) -> None:
        """Record the identifier of the affected target record."""
        self.target_id = target_id

    def add_metadata(self, **fields: Any) -> None:
        """Merge additional contextual fields into the entry's metadata."""
        self.metadata.update(fields)

    def skip(self) -> None:
        """Suppress recording for this request."""
        self._skip = True

    @property
    def skipped(self) -> bool:
        return self._skip


def audited(
    action: str,
    target_type: str,
) -> Callable[..., Coroutine[Any, Any, AuditContext]]:
    """Build a router-boundary dependency that records an audit entry.

    Usage in a mutating handler::

        @router.post("/merchants/{merchant_id}/suspend")
        async def suspend_merchant(
            merchant_id: uuid.UUID,
            audit: Annotated[AuditContext, Depends(audited("merchants.suspend", "merchant"))],
            ...
        ):
            ...
            audit.set_target(merchant_id)
            return result

    The returned dependency resolves the current admin (the actor) and a
    database session, yields an :class:`AuditContext` for the handler to fill
    in, and — after the handler completes — writes one :class:`AuditLog` row.
    If the handler raises, the entry is still recorded with a ``failure``
    outcome before the exception propagates, unless the handler explicitly
    called :meth:`AuditContext.skip`.
    """

    async def _dependency(
        db: Annotated[AsyncSession, Depends(get_db)],
        actor: Annotated[AdminAccount, Depends(get_current_admin)],
    ):
        context = AuditContext(action=action, target_type=target_type)
        service = AuditService(db)
        try:
            yield context
        except Exception:
            # The handler failed mid-mutation: record the attempt as a failure
            # (best-effort) and re-raise so normal error handling still applies.
            if not context.skipped:
                context.outcome = OUTCOME_FAILURE
                await service.record(
                    actor=actor,
                    action=context.action,
                    target_type=context.target_type,
                    target_id=context.target_id,
                    outcome=context.outcome,
                    metadata=context.metadata,
                )
            raise
        else:
            if not context.skipped:
                await service.record(
                    actor=actor,
                    action=context.action,
                    target_type=context.target_type,
                    target_id=context.target_id,
                    outcome=context.outcome,
                    metadata=context.metadata,
                )

    return _dependency

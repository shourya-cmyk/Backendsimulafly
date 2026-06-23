"""Response schema for the immutable audit log (Requirement 19).

The audit log is an insert-only record of admin actions (see
:class:`app.models.admin.AuditLog`). This module exposes the read projection
returned by ``GET /admin/audit`` wrapped in the uniform
:class:`~app.schemas.admin.listing.ListingEnvelope`.

The underlying model stores the free-form payload on the python attribute
``audit_metadata`` (the column is named ``metadata``, which is reserved on the
declarative ``Base``). :class:`AuditEntryOut` reads that attribute via a
validation alias and serialises it back out under the ``metadata`` key so the
Admin Panel sees the natural field name.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AuditEntryOut(BaseModel):
    """One immutable audit entry as returned by ``GET /admin/audit`` (R19.2).

    Attributes:
        id: Identifier of the audit entry.
        actor_admin_id: The acting :class:`~app.models.admin.AdminAccount`, or
            ``None`` when the actor was removed (the FK is ``SET NULL``).
        action: The action key (e.g. ``merchants.suspend``).
        target_type: The kind of resource the action targeted.
        target_id: The targeted resource identifier, when applicable.
        outcome: The recorded outcome of the action.
        metadata: Free-form salient details captured for the action. Read from
            the model's ``audit_metadata`` attribute; serialised as ``metadata``.
        created_at: When the entry was recorded.
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID
    actor_admin_id: uuid.UUID | None = None
    action: str
    target_type: str
    target_id: str | None = None
    outcome: str
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias="audit_metadata",
        serialization_alias="metadata",
    )
    created_at: datetime

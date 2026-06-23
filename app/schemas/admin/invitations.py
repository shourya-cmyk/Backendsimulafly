"""Request/response schemas for admin invitation create & activation (R3).

These back the (separately implemented) ``/invitations`` and
``/invitations/activate`` endpoints:

- :class:`InvitationCreateRequest` — a Super Admin invites a new admin by
  ``email`` and one or more ``role_ids`` (Requirement 3.1).
- :class:`InvitationCreateResponse` — returns the created invitation metadata
  **including the raw token** (returned exactly once to the caller; only its
  hash is ever persisted).
- :class:`InvitationActivateRequest` — the invited user activates with the raw
  ``token`` and a chosen ``password`` (Requirements 3.2, 3.3).
- :class:`InvitationActivateResponse` — the now-active account's summary.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class InvitationCreateRequest(BaseModel):
    """Payload for ``POST /invitations`` (Requirement 3.1).

    ``role_ids`` must contain at least one role; an empty list is rejected with
    HTTP 422 (enforced both here and defensively in the service layer).
    """

    email: EmailStr
    role_ids: list[uuid.UUID] = Field(min_length=1)


class InvitationCreateResponse(BaseModel):
    """Result of creating an invitation (Requirement 3.1).

    ``token`` is the **raw**, single-use invitation token. It is returned to the
    caller here and never stored in raw form (only its SHA-256 hash lives on
    ``AdminInvitation.token_hash``).
    """

    invitation_id: uuid.UUID
    account_id: uuid.UUID
    email: EmailStr
    role_ids: list[uuid.UUID]
    token: str
    status: str
    expires_at: datetime


class InvitationActivateRequest(BaseModel):
    """Payload for ``POST /invitations/activate`` (Requirements 3.2, 3.3)."""

    token: str = Field(min_length=1)
    password: str = Field(min_length=8, max_length=128)


class InvitationActivateResponse(BaseModel):
    """Summary of the activated admin account (Requirement 3.2)."""

    model_config = ConfigDict(from_attributes=True)

    account_id: uuid.UUID
    email: EmailStr
    is_active: bool
    role_ids: list[uuid.UUID]

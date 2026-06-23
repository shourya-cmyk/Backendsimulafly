"""Request/response schemas for RBAC role management (R2).

These back the ``rbac.py`` router:

- :class:`RoleResponse` / :class:`RolesListResponse` — list predefined and
  custom roles with their permission keys (Requirements 2.1, 2.2).
- :class:`RoleCreateRequest` — create a custom role (Requirement 2.8).
- :class:`RoleUpdateRequest` — modify a role's permissions/metadata
  (Requirement 2.6).

Permissions are addressed by their stable ``key`` string (e.g.
``wallets.adjust``), matching the seeded permission catalog.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field


class RoleResponse(BaseModel):
    """A single role with the permission keys it grants (R2.1, R2.2)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None = None
    is_predefined: bool
    permissions: list[str]


class RolesListResponse(BaseModel):
    """The full set of roles (predefined + custom)."""

    roles: list[RoleResponse]


class RoleCreateRequest(BaseModel):
    """Payload for ``POST /roles`` — create a custom role (R2.8)."""

    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    permissions: list[str] = Field(default_factory=list)


class RoleUpdateRequest(BaseModel):
    """Payload for ``PATCH /roles/{id}`` — modify a role (R2.6).

    Every field is optional; only those provided are changed. ``permissions``,
    when present, fully replaces the role's permission set.
    """

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    permissions: list[str] | None = None

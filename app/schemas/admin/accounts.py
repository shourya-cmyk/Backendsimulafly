"""Request/response schemas for admin account listing and multi-account switch.

These back Requirement 4 (multi-account view & switch). The switch endpoint
reuses the :class:`app.schemas.admin.auth.TokenPair` contract so the Admin Panel
consumes a freshly scoped token pair exactly as it does after login/refresh.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, EmailStr


class LinkedAccount(BaseModel):
    """A single linked admin account exposed by ``GET /admin/accounts`` (R4.1).

    Carries the account ``id`` (identifier), ``email``, the names of its
    associated roles, and its active status.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    roles: list[str]
    is_active: bool


class LinkedAccountsResponse(BaseModel):
    """The list of accounts linked to the authenticated identity (R4.1, R4.2).

    When no accounts are linked beyond the authenticated identity, ``accounts``
    is an empty list rather than an error (R4.2).
    """

    accounts: list[LinkedAccount]


class AssignRolesRequest(BaseModel):
    """Payload for ``POST /accounts/{id}/roles`` (R2.5).

    ``role_ids`` replaces the account's current role set. An empty list clears
    all roles; every id must reference an existing role (else HTTP 404).
    """

    role_ids: list[uuid.UUID]


class AccountListItemSchema(BaseModel):
    """A single row in the paginated admin-accounts listing (R3.4)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    role_names: list[str]
    is_active: bool


class AccountPageResponse(BaseModel):
    """Pagination envelope for ``GET /accounts`` (R3.4)."""

    model_config = ConfigDict(from_attributes=True)

    items: list[AccountListItemSchema]
    page: int
    page_size: int
    total: int
    total_pages: int
    has_next: bool
    next_page: int | None = None


class AccountStatusResponse(BaseModel):
    """Result of a status transition / role assignment on an admin account.

    Backs deactivate/reactivate (R3.5, R3.6) and assign-roles (R2.5).
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    is_active: bool
    roles: list[str]

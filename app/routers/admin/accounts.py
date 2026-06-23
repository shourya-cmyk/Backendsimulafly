"""Admin accounts router — account management, invitations, multi-account (R2/R3/R4).

Endpoints (all prefixed ``/admin``; mounted under ``/api/v1`` by the wiring
step):

| Method | Path                          | Permission        | Req       |
|--------|-------------------------------|-------------------|-----------|
| GET    | `/accounts`                   | `accounts.read`   | 3.4       |
| POST   | `/accounts/{id}/roles`        | `accounts.manage` | 2.5       |
| POST   | `/accounts/{id}/deactivate`   | `accounts.manage` | 3.5, 3.7  |
| POST   | `/accounts/{id}/reactivate`   | `accounts.manage` | 3.6       |
| POST   | `/invitations`                | `accounts.manage` | 3.1       |
| POST   | `/invitations/activate`       | none              | 3.2, 3.3  |
| GET    | `/accounts/linked`            | authenticated     | 4.1, 4.2  |
| POST   | `/accounts/{id}/switch`       | authenticated     | 4.3–4.6   |

The design's inventory lists linked accounts at ``GET /accounts``; to avoid a
collision with the management ``GET /accounts`` listing (R3.4), linked accounts
are exposed at ``GET /accounts/linked`` here. Reads/mutations are gated by
``require_permission(...)``; mutating routes are wrapped by ``audited(...)`` so
each writes one immutable audit entry (Requirement 19.1). Invitation activation
is intentionally unauthenticated (an invited user has no token yet).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.admin import AdminAccount
from app.schemas.admin.accounts import (
    AccountPageResponse,
    AccountStatusResponse,
    AssignRolesRequest,
    LinkedAccountsResponse,
)
from app.schemas.admin.auth import TokenPair
from app.schemas.admin.invitations import (
    InvitationActivateRequest,
    InvitationActivateResponse,
    InvitationCreateRequest,
    InvitationCreateResponse,
)
from app.services.admin.accounts_service import (
    AccountListParams,
    AdminAccountsService,
)
from app.services.admin.audit_service import AuditContext, audited
from app.services.admin.invitations_service import AdminInvitationsService
from app.services.admin.multi_account_service import MultiAccountService
from app.utils.admin_dependencies import get_current_admin, require_permission

router = APIRouter(prefix="/admin", tags=["admin:accounts"])

DBSession = Annotated[AsyncSession, Depends(get_db)]


def _to_status_response(account: AdminAccount) -> AccountStatusResponse:
    return AccountStatusResponse(
        id=account.id,
        email=account.email,
        is_active=account.is_active,
        roles=sorted(role.name for role in account.roles),
    )


# -- Account management (R3.4, R3.5, R3.6, R2.5) ---------------------------


@router.get(
    "/accounts",
    response_model=AccountPageResponse,
    dependencies=[Depends(require_permission("accounts.read"))],
)
async def list_accounts(
    db: DBSession,
    page: int = Query(default=1, ge=1),
    page_size: int | None = Query(default=None, ge=1),
) -> AccountPageResponse:
    """Paginated admin accounts: id, email, role names, active status (R3.4)."""
    page_obj = await AdminAccountsService(db).list_accounts(
        AccountListParams(page=page, page_size=page_size)
    )
    return AccountPageResponse.model_validate(page_obj)


@router.post(
    "/accounts/{account_id}/roles",
    response_model=AccountStatusResponse,
    dependencies=[Depends(require_permission("accounts.manage"))],
)
async def assign_roles(
    account_id: uuid.UUID,
    payload: AssignRolesRequest,
    db: DBSession,
    audit: Annotated[AuditContext, Depends(audited("accounts.assign_roles", "admin_account"))],
) -> AccountStatusResponse:
    """Replace an account's assigned roles (R2.5)."""
    account = await AdminAccountsService(db).assign_roles(account_id, payload.role_ids)
    audit.set_target(account_id)
    audit.add_metadata(role_ids=[str(r) for r in payload.role_ids])
    return _to_status_response(account)


@router.post(
    "/accounts/{account_id}/deactivate",
    response_model=AccountStatusResponse,
    dependencies=[Depends(require_permission("accounts.manage"))],
)
async def deactivate_account(
    account_id: uuid.UUID,
    db: DBSession,
    audit: Annotated[AuditContext, Depends(audited("accounts.deactivate", "admin_account"))],
) -> AccountStatusResponse:
    """Deactivate an account; refuses the last active Super Admin with 409 (R3.5, R3.7)."""
    account = await AdminAccountsService(db).deactivate_account(account_id)
    audit.set_target(account_id)
    return _to_status_response(account)


@router.post(
    "/accounts/{account_id}/reactivate",
    response_model=AccountStatusResponse,
    dependencies=[Depends(require_permission("accounts.manage"))],
)
async def reactivate_account(
    account_id: uuid.UUID,
    db: DBSession,
    audit: Annotated[AuditContext, Depends(audited("accounts.reactivate", "admin_account"))],
) -> AccountStatusResponse:
    """Reactivate an account (R3.6)."""
    account = await AdminAccountsService(db).reactivate_account(account_id)
    audit.set_target(account_id)
    return _to_status_response(account)


# -- Invitations (R3.1, R3.2, R3.3) ----------------------------------------


@router.post(
    "/invitations",
    response_model=InvitationCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_invitation(
    payload: InvitationCreateRequest,
    db: DBSession,
    actor: Annotated[AdminAccount, Depends(require_permission("accounts.manage"))],
    audit: Annotated[AuditContext, Depends(audited("invitations.create", "admin_invitation"))],
) -> InvitationCreateResponse:
    """Invite a new admin: create a pending account + invitation (R3.1)."""
    result = await AdminInvitationsService(db).create_invitation(
        payload.email,
        payload.role_ids,
        created_by=actor.id,
    )
    audit.set_target(result.invitation_id)
    audit.add_metadata(email=result.email, role_ids=[str(r) for r in result.role_ids])
    return result


@router.post(
    "/invitations/activate",
    response_model=InvitationActivateResponse,
)
async def activate_invitation(
    payload: InvitationActivateRequest,
    db: DBSession,
) -> InvitationActivateResponse:
    """Activate an account from an invitation token (R3.2, R3.3).

    Intentionally unauthenticated: the invited user has no admin token yet. An
    expired or already-used token is refused with HTTP 400 by the service.
    """
    return await AdminInvitationsService(db).activate_invitation(
        payload.token, payload.password
    )


# -- Multi-account view & switch (R4) --------------------------------------


@router.get(
    "/accounts/linked",
    response_model=LinkedAccountsResponse,
)
async def list_linked_accounts(
    db: DBSession,
    actor: Annotated[AdminAccount, Depends(get_current_admin)],
) -> LinkedAccountsResponse:
    """List accounts linked to the authenticated identity; empty list if none (R4.1, R4.2)."""
    accounts = await MultiAccountService(db).list_linked_accounts(actor)
    return LinkedAccountsResponse(accounts=accounts)


@router.post(
    "/accounts/{account_id}/switch",
    response_model=TokenPair,
)
async def switch_account(
    account_id: uuid.UUID,
    db: DBSession,
    actor: Annotated[AdminAccount, Depends(get_current_admin)],
) -> TokenPair:
    """Switch to a linked, active account, issuing a scoped token pair (R4.3–R4.6)."""
    return await MultiAccountService(db).switch(actor, account_id)

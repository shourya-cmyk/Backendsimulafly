"""Admin authentication endpoints (R1, R2.7, R24.1).

Exposes the brand-new admin login flow, distinct from the consumer ``/auth``
router. All routes are mounted under ``/admin`` and delegate to
:class:`app.services.admin.auth_service.AdminAuthService`:

- ``POST /admin/auth/login`` and ``POST /admin/auth/refresh`` are
  **unauthenticated** (they establish/rotate a session) — R1.1–R1.4.
- ``POST /admin/auth/logout`` requires a valid admin access token and revokes
  the presented refresh-token session — R1.5, R24.1.
- ``GET /admin/auth/me/permissions`` returns the current admin's *effective*
  permission set (the union over assigned roles) computed by
  :class:`app.services.admin.rbac_service.AdminRBACService` — R2.7.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.schemas.admin.auth import (
    LoginRequest,
    LogoutRequest,
    LogoutResponse,
    PermissionsResponse,
    RefreshRequest,
    TokenPair,
)
from app.services.admin.auth_service import AdminAuthService
from app.services.admin.rbac_service import AdminRBACService
from app.utils.admin_dependencies import CurrentAdmin, DBSession

router = APIRouter(prefix="/admin", tags=["admin-auth"])


@router.post("/auth/login", response_model=TokenPair)
async def login(body: LoginRequest, db: DBSession) -> TokenPair:
    """Authenticate admin credentials and issue a token pair (R1.1, R1.2).

    Unauthenticated. Invalid credentials → ``401``; a locked-out account →
    ``423`` (enforced by the service).
    """
    return await AdminAuthService(db).login(body.email, body.password)


@router.post("/auth/refresh", response_model=TokenPair)
async def refresh(body: RefreshRequest, db: DBSession) -> TokenPair:
    """Rotate a refresh token into a fresh token pair (R1.3, R1.4).

    Unauthenticated. Expired/revoked/unknown refresh tokens → ``401``.
    """
    return await AdminAuthService(db).refresh(body.refresh_token)


@router.post("/auth/logout", response_model=LogoutResponse)
async def logout(
    body: LogoutRequest,
    db: DBSession,
    admin: CurrentAdmin,
) -> LogoutResponse:
    """Revoke the presented refresh-token session (R1.5, R24.1).

    Requires a valid admin access token. Idempotent — revoking an unknown or
    already-revoked token still reports success.
    """
    revoked = await AdminAuthService(db).logout(body.refresh_token)
    return LogoutResponse(revoked=revoked)


@router.get("/auth/me/permissions", response_model=PermissionsResponse)
async def my_permissions(
    db: DBSession,
    admin: CurrentAdmin,
) -> PermissionsResponse:
    """Return the current admin's effective permission set (R2.7).

    The set is the union of permission keys across the account's assigned
    roles (the Super Admin wildcard ``*`` is reported verbatim).
    """
    effective = await AdminRBACService(db).load_effective_permissions(admin.id)
    return PermissionsResponse(permissions=sorted(effective))

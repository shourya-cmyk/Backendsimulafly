"""FastAPI dependencies for admin authentication and authorization.

Mirrors :mod:`app.utils.dependencies` (consumer-side auth) but resolves the
admin identity from an *admin-audience* access token (see
:mod:`app.core.admin_security`) and gates access on the account's *effective*
permission set computed by :class:`app.services.admin.rbac_service.AdminRBACService`.

- :func:`get_current_admin` decodes the Bearer access token, enforces the admin
  audience/expiry/type via :func:`decode_admin_token`, and loads the active
  :class:`AdminAccount`. Any failure (malformed/expired/wrong-audience token,
  missing/inactive/soft-deleted account) raises ``401``.
- :func:`require_permission` is a dependency *factory*: it returns a dependency
  that resolves the current admin, computes effective permissions, and raises
  ``403`` when the required permission(s) are not satisfied (the wildcard ``*``
  honored by the RBAC service). On success it returns the ``AdminAccount``.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Callable, Coroutine, Any

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.admin_security import TokenError, decode_admin_token
from app.core.database import get_db
from app.models.admin import AdminAccount
from app.services.admin.rbac_service import (
    AdminRBACService,
    is_permission_satisfied,
)

# Use the same Bearer scheme style as the consumer-side dependency so the admin
# API presents an identical "Authorization: Bearer <token>" contract.
bearer_scheme = HTTPBearer(auto_error=True)

DBSession = Annotated[AsyncSession, Depends(get_db)]


async def get_current_admin(
    db: DBSession,
    creds: Annotated[HTTPAuthorizationCredentials, Depends(bearer_scheme)],
) -> AdminAccount:
    """Resolve the authenticated admin from the Bearer access token.

    Decodes the credentials as an admin-audience *access* token and loads the
    matching active :class:`AdminAccount`. Raises ``401`` when the token is
    malformed, expired, carries the wrong audience or type, or when the account
    is missing, inactive, or soft-deleted.
    """
    try:
        claims = decode_admin_token(creds.credentials, "access")
    except TokenError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e)
        )

    try:
        account_id = uuid.UUID(claims.account_id)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid subject"
        )

    account = await db.get(AdminAccount, account_id)
    if account is None or not account.is_active or account.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="admin not found"
        )
    return account


CurrentAdmin = Annotated[AdminAccount, Depends(get_current_admin)]


def require_permission(
    *needed: str,
) -> Callable[..., Coroutine[Any, Any, AdminAccount]]:
    """Build a dependency enforcing that the admin holds the given permission(s).

    The returned dependency resolves the current admin via
    :func:`get_current_admin`, computes the account's effective permission set
    via :class:`AdminRBACService`, and raises ``403`` when the required
    permission(s) are not satisfied. The wildcard ``*`` (held by Super Admin)
    satisfies any requirement. On success the :class:`AdminAccount` is returned,
    so handlers can declare it as the dependency value.

    Multiple permissions are conjunctive: every key must be present (unless the
    account holds the wildcard).
    """

    async def _dependency(
        db: DBSession,
        account: CurrentAdmin,
    ) -> AdminAccount:
        service = AdminRBACService(db)
        effective = await service.load_effective_permissions(account.id)
        if not is_permission_satisfied(effective, needed):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="insufficient permissions",
            )
        return account

    return _dependency

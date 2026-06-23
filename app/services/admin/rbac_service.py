"""Admin RBAC resolution — effective permission computation and checks.

The Admin_RBAC_Service computes an AdminAccount's *effective permission set*
as the union of the permission keys granted by each of its assigned roles
(Requirements 2.3, 2.5, 2.6). A role granting the wildcard permission ``*``
satisfies any permission check (mirroring the seeded Super Admin role).

Authorization decisions consult **only** the effective permission set, never a
role's name, so custom roles resolve identically to predefined ones
(Requirement 2.8). The effective-permissions endpoint (Requirement 2.7) returns
the same set computed here.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.admin import AdminAccount, Role

#: Permission key that satisfies every permission check.
WILDCARD_PERMISSION = "*"


def is_permission_satisfied(
    effective: set[str],
    required: str | Iterable[str],
) -> bool:
    """Return True if ``required`` is satisfied by the ``effective`` set.

    The wildcard permission ``*`` present in ``effective`` satisfies any
    requirement. Otherwise every required key must be present in the effective
    set (deny-by-default, conjunctive over multiple required permissions).

    Resolution only ever inspects permission key strings — never role names —
    so predefined and custom roles are treated identically (Requirement 2.8).
    """
    if WILDCARD_PERMISSION in effective:
        return True

    if isinstance(required, str):
        needed: set[str] = {required}
    else:
        needed = set(required)

    # An empty requirement is trivially satisfied (no permission gate).
    return needed.issubset(effective)


def effective_permissions_from_roles(roles: Iterable[Role]) -> set[str]:
    """Compute the union of permission keys across the given roles.

    Pure helper over already-loaded ``Role`` objects (each with its
    ``permissions`` relationship populated). Used by the service after loading
    an account's roles, and directly testable without a database session.
    """
    effective: set[str] = set()
    for role in roles:
        for permission in role.permissions:
            effective.add(permission.key)
    return effective


class AdminRBACService:
    """Resolves effective permissions for admin accounts."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def load_effective_permissions(
        self,
        account_id: uuid.UUID,
    ) -> set[str]:
        """Load an account's effective permission set by identifier.

        Returns the union of every assigned role's permission keys. An account
        with no roles (or no permissions) yields an empty set, which satisfies
        no non-empty permission check (deny-by-default). Returns an empty set
        when the account does not exist.
        """
        stmt = (
            select(AdminAccount)
            .where(AdminAccount.id == account_id)
            .options(selectinload(AdminAccount.roles).selectinload(Role.permissions))
        )
        result = await self.db.execute(stmt)
        account = result.scalar_one_or_none()
        if account is None:
            return set()
        return effective_permissions_from_roles(account.roles)

    async def effective_permissions_for(
        self,
        account: AdminAccount,
    ) -> set[str]:
        """Compute the effective permission set for a loaded ``AdminAccount``.

        Ensures the ``roles`` and nested ``permissions`` relationships are
        loaded (issuing a query if they were lazy/unloaded) before unioning
        their permission keys.
        """
        stmt = (
            select(Role)
            .join(Role.accounts)
            .where(AdminAccount.id == account.id)
            .options(selectinload(Role.permissions))
        )
        result = await self.db.execute(stmt)
        roles = result.scalars().unique().all()
        return effective_permissions_from_roles(roles)

    async def has_permissions(
        self,
        account_id: uuid.UUID,
        required: str | Iterable[str],
    ) -> bool:
        """Return True if the account's effective set satisfies ``required``.

        Accounts for the ``*`` wildcard. Convenience wrapper combining
        :meth:`load_effective_permissions` with :func:`is_permission_satisfied`.
        """
        effective = await self.load_effective_permissions(account_id)
        return is_permission_satisfied(effective, required)

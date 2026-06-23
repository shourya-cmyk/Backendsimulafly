"""Admin roles service — list, create (custom), and modify roles (R2).

Backs the ``rbac.py`` router endpoints:

  * **List** (Requirements 2.1, 2.2): return every role — predefined and custom
    — together with the permission keys it grants.
  * **Create** (Requirement 2.8): create a *custom* role from ``{name,
    description, permissions[]}``. The role is non-predefined; a duplicate name
    is refused with HTTP 409; an unknown permission key is refused with HTTP
    422.
  * **Update** (Requirement 2.6): modify a role's permission set (and,
    optionally, its name/description). Unknown role → 404; unknown permission
    key → 422; a name collision with another role → 409.

Permissions are addressed by their stable ``key`` string (e.g. ``wallets.adjust``)
which is what the seed catalog (``rbac_seed.py``) and the RBAC resolver
(``rbac_service.py``) operate on. Authorization never consults a role's *name*,
so custom and predefined roles resolve identically (Requirement 2.8).
"""
from __future__ import annotations

import uuid
from collections.abc import Iterable

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.admin import Permission, Role


class AdminRolesService:
    """List/create/update operations over :class:`Role` and :class:`Permission`."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # -- Listing -----------------------------------------------------------

    async def list_roles(self) -> list[Role]:
        """Return all roles (predefined + custom) with permissions loaded.

        Ordered by name for a deterministic listing. Each role's
        ``permissions`` relationship is eagerly loaded so the router can project
        the permission keys without further queries.
        """
        stmt = (
            select(Role)
            .options(selectinload(Role.permissions))
            .order_by(Role.name)
        )
        return list((await self.db.execute(stmt)).scalars().unique().all())

    # -- Create ------------------------------------------------------------

    async def create_role(
        self,
        name: str,
        permission_keys: Iterable[str],
        *,
        description: str | None = None,
    ) -> Role:
        """Create a custom role (Requirement 2.8).

        The new role is ``is_predefined=False``. A blank or duplicate name is
        refused (422 / 409); any permission key that is not in the catalog is
        refused with HTTP 422.
        """
        normalized = (name or "").strip()
        if not normalized:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="role name must not be empty",
            )

        existing = (
            await self.db.execute(select(Role).where(Role.name == normalized))
        ).scalar_one_or_none()
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="a role with this name already exists",
            )

        permissions = await self._resolve_permissions(permission_keys)

        role = Role(
            name=normalized,
            description=description,
            is_predefined=False,
        )
        role.permissions = permissions
        self.db.add(role)
        await self.db.commit()
        await self.db.refresh(role, attribute_names=["permissions"])
        return role

    # -- Update ------------------------------------------------------------

    async def update_role(
        self,
        role_id: uuid.UUID,
        *,
        permission_keys: Iterable[str] | None = None,
        name: str | None = None,
        description: str | None = None,
    ) -> Role:
        """Modify a role's permissions and/or metadata (Requirement 2.6).

        ``permission_keys`` (when provided) fully replaces the role's permission
        set. Unknown role → 404; unknown permission key → 422; renaming to a
        name already held by another role → 409. Fields left as ``None`` are
        unchanged.
        """
        role = await self._get_role_or_404(role_id)

        if name is not None:
            normalized = name.strip()
            if not normalized:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="role name must not be empty",
                )
            if normalized != role.name:
                clash = (
                    await self.db.execute(
                        select(Role).where(
                            Role.name == normalized, Role.id != role_id
                        )
                    )
                ).scalar_one_or_none()
                if clash is not None:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="a role with this name already exists",
                    )
            role.name = normalized

        if description is not None:
            role.description = description

        if permission_keys is not None:
            role.permissions = await self._resolve_permissions(permission_keys)

        await self.db.commit()
        await self.db.refresh(role, attribute_names=["permissions"])
        return role

    # -- Internal helpers --------------------------------------------------

    async def _get_role_or_404(self, role_id: uuid.UUID) -> Role:
        stmt = (
            select(Role)
            .where(Role.id == role_id)
            .options(selectinload(Role.permissions))
        )
        role = (await self.db.execute(stmt)).scalar_one_or_none()
        if role is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="role not found",
            )
        return role

    async def _resolve_permissions(
        self,
        permission_keys: Iterable[str],
    ) -> list[Permission]:
        """Resolve permission keys to rows; unknown keys → HTTP 422.

        De-duplicates the input. An empty set is valid (a role granting no
        permissions).
        """
        unique_keys = list(dict.fromkeys(k for k in permission_keys if k))
        if not unique_keys:
            return []

        permissions = (
            (
                await self.db.execute(
                    select(Permission).where(Permission.key.in_(unique_keys))
                )
            )
            .scalars()
            .unique()
            .all()
        )
        found = {p.key for p in permissions}
        missing = [k for k in unique_keys if k not in found]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"unknown permission key(s): {', '.join(missing)}",
            )
        return list(permissions)

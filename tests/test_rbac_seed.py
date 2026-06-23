"""Unit tests for the idempotent RBAC seed (Requirements 2.1, 2.2).

Covers:
  * Running ``_seed`` twice creates no duplicate roles or permissions.
  * The four predefined roles (Super Admin, Org Manager, Finance, Support)
    exist after seeding, and Super Admin holds the wildcard ``*`` permission.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.models.admin import Permission, Role
from app.services.admin.rbac_seed import (
    PERMISSION_CATALOG,
    PREDEFINED_ROLES,
    _seed,
    seed_rbac_if_empty,
)
from app.services.admin.rbac_service import WILDCARD_PERMISSION

PREDEFINED_ROLE_NAMES = {"Super Admin", "Org Manager", "Finance", "Support"}


async def _count(db_session, model) -> int:
    result = await db_session.execute(select(func.count()).select_from(model))
    return int(result.scalar() or 0)


@pytest.mark.asyncio
async def test_seed_creates_four_predefined_roles(db_session):
    created = await _seed(db_session)

    assert created == len(PREDEFINED_ROLES) == 4

    names = {
        r.name
        for r in (await db_session.execute(select(Role))).scalars()
    }
    assert names == PREDEFINED_ROLE_NAMES


@pytest.mark.asyncio
async def test_super_admin_holds_wildcard_permission(db_session):
    await _seed(db_session)

    super_admin = (
        await db_session.execute(
            select(Role)
            .where(Role.name == "Super Admin")
            .options(selectinload(Role.permissions))
        )
    ).scalar_one()

    keys = {p.key for p in super_admin.permissions}
    assert WILDCARD_PERMISSION in keys
    assert keys == {WILDCARD_PERMISSION}


@pytest.mark.asyncio
async def test_seed_seeds_full_permission_catalog(db_session):
    await _seed(db_session)

    keys = {
        p.key
        for p in (await db_session.execute(select(Permission))).scalars()
    }
    expected = set(PERMISSION_CATALOG) | {WILDCARD_PERMISSION}
    assert keys == expected


@pytest.mark.asyncio
async def test_seed_is_idempotent_no_duplicates(db_session):
    # First seed.
    await _seed(db_session)
    roles_after_first = await _count(db_session, Role)
    perms_after_first = await _count(db_session, Permission)

    # Count role->permission grant pairs after the first seed.
    grants_after_first = sum(
        len(r.permissions)
        for r in (
            await db_session.execute(
                select(Role).options(selectinload(Role.permissions))
            )
        ).scalars()
    )

    # Second seed must not create any duplicates.
    created = await _seed(db_session)
    assert created == 0

    assert await _count(db_session, Role) == roles_after_first
    assert await _count(db_session, Permission) == perms_after_first

    grants_after_second = sum(
        len(r.permissions)
        for r in (
            await db_session.execute(
                select(Role).options(selectinload(Role.permissions))
            )
        ).scalars()
    )
    assert grants_after_second == grants_after_first


@pytest.mark.asyncio
async def test_seed_rbac_if_empty_skips_when_populated(db_session):
    # Seed once to populate roles.
    await _seed(db_session)
    roles_before = await _count(db_session, Role)

    # The fast-path guard should detect existing rows and no-op.
    created = await seed_rbac_if_empty(db_session)
    assert created == 0
    assert await _count(db_session, Role) == roles_before

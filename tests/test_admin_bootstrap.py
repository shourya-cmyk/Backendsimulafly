"""Tests for the first-run Super Admin bootstrap.

Covers:
  * ``bootstrap_super_admin`` creates exactly one active Super Admin account
    with the predefined ``Super Admin`` role when configured.
  * It is idempotent — re-running does not duplicate the account and does NOT
    reset the existing password.
  * It no-ops when the bootstrap env vars are not configured.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.security import verify_password
from app.models.admin import AdminAccount
from app.services.admin.admin_bootstrap import (
    SUPER_ADMIN_ROLE_NAME,
    bootstrap_super_admin,
)
from app.services.admin.rbac_seed import seed_rbac_if_empty

BOOTSTRAP_EMAIL = "bootstrap-admin@example.com"
BOOTSTRAP_PASSWORD = "BootstrapPass123!"


@pytest.fixture
def _configure_bootstrap(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "ADMIN_BOOTSTRAP_EMAIL", BOOTSTRAP_EMAIL)
    monkeypatch.setattr(settings, "ADMIN_BOOTSTRAP_PASSWORD", BOOTSTRAP_PASSWORD)
    monkeypatch.setattr(settings, "ADMIN_BOOTSTRAP_FULL_NAME", "Boot Admin")
    return settings


async def _account_count(db_session) -> int:
    result = await db_session.execute(select(func.count()).select_from(AdminAccount))
    return int(result.scalar() or 0)


@pytest.mark.asyncio
async def test_bootstrap_creates_single_super_admin(db_session, _configure_bootstrap):
    await seed_rbac_if_empty(db_session)

    created = await bootstrap_super_admin(db_session)
    assert created is True

    assert await _account_count(db_session) == 1

    account = (
        await db_session.execute(
            select(AdminAccount)
            .where(AdminAccount.email == BOOTSTRAP_EMAIL)
            .options(selectinload(AdminAccount.roles))
        )
    ).scalar_one()

    assert account.is_active is True
    assert account.full_name == "Boot Admin"
    assert verify_password(BOOTSTRAP_PASSWORD, account.hashed_password)
    assert {r.name for r in account.roles} == {SUPER_ADMIN_ROLE_NAME}


@pytest.mark.asyncio
async def test_bootstrap_is_idempotent_no_duplicate_or_reset(
    db_session, _configure_bootstrap
):
    await seed_rbac_if_empty(db_session)

    assert await bootstrap_super_admin(db_session) is True
    original = (
        await db_session.execute(
            select(AdminAccount).where(AdminAccount.email == BOOTSTRAP_EMAIL)
        )
    ).scalar_one()
    original_hash = original.hashed_password

    # Second run: no new account, password untouched.
    created_again = await bootstrap_super_admin(db_session)
    assert created_again is False
    assert await _account_count(db_session) == 1

    refetched = (
        await db_session.execute(
            select(AdminAccount).where(AdminAccount.email == BOOTSTRAP_EMAIL)
        )
    ).scalar_one()
    assert refetched.hashed_password == original_hash


@pytest.mark.asyncio
async def test_bootstrap_noops_when_unconfigured(db_session, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "ADMIN_BOOTSTRAP_EMAIL", "")
    monkeypatch.setattr(settings, "ADMIN_BOOTSTRAP_PASSWORD", "")

    await seed_rbac_if_empty(db_session)
    created = await bootstrap_super_admin(db_session)

    assert created is False
    assert await _account_count(db_session) == 0

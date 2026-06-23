"""Unit tests for the Admin_RBAC_Service effective-permission union logic.

Covers the pure resolution helpers and the DB-backed loader:
- Union of permission keys across multiple assigned roles (Requirement 2.5).
- The wildcard permission ``*`` satisfies any permission check.
- Deny-by-default when a required permission is absent (Requirement 2.6).
- Conjunctive evaluation across multiple required permissions.
- Custom roles resolve identically to predefined ones — authorization
  consults only permission keys, never role names (Requirement 2.8).

The pure helpers (:func:`effective_permissions_from_roles`,
:func:`is_permission_satisfied`) are exercised directly without a database;
:meth:`AdminRBACService.load_effective_permissions` and
:meth:`AdminRBACService.has_permissions` are exercised against seeded
Role/Permission rows using the async ``db_session`` fixture.

Requirements: 2.5, 2.6, 2.8
"""

from __future__ import annotations

import uuid

import pytest_asyncio

from app.models.admin import AdminAccount, Permission, Role
from app.services.admin.rbac_service import (
    WILDCARD_PERMISSION,
    AdminRBACService,
    effective_permissions_from_roles,
    is_permission_satisfied,
)


# ---------------------------------------------------------------------------
# Helpers for building in-memory Role/Permission graphs for the pure tests.
# ---------------------------------------------------------------------------


def _make_role(name: str, keys: list[str], *, is_predefined: bool = False) -> Role:
    """Build a detached Role with attached Permission objects (no DB)."""
    role = Role(name=name, is_predefined=is_predefined)
    role.permissions = [Permission(key=key) for key in keys]
    return role


# ---------------------------------------------------------------------------
# effective_permissions_from_roles — union across roles (Requirement 2.5)
# ---------------------------------------------------------------------------


def test_union_across_multiple_roles_collects_all_keys():
    finance = _make_role("Finance", ["wallets.adjust", "invoices.read"])
    support = _make_role("Support", ["tickets.read", "tickets.respond"])

    effective = effective_permissions_from_roles([finance, support])

    assert effective == {
        "wallets.adjust",
        "invoices.read",
        "tickets.read",
        "tickets.respond",
    }


def test_union_deduplicates_overlapping_permissions():
    role_a = _make_role("A", ["merchants.read", "merchants.suspend"])
    role_b = _make_role("B", ["merchants.read", "audit.read"])

    effective = effective_permissions_from_roles([role_a, role_b])

    # Overlapping `merchants.read` appears once; union is the distinct set.
    assert effective == {"merchants.read", "merchants.suspend", "audit.read"}


def test_no_roles_yields_empty_set():
    assert effective_permissions_from_roles([]) == set()


def test_role_with_no_permissions_contributes_nothing():
    empty = _make_role("Empty", [])
    finance = _make_role("Finance", ["wallets.adjust"])

    assert effective_permissions_from_roles([empty, finance]) == {"wallets.adjust"}


# ---------------------------------------------------------------------------
# is_permission_satisfied — wildcard satisfies any check
# ---------------------------------------------------------------------------


def test_wildcard_satisfies_single_permission():
    effective = {WILDCARD_PERMISSION}

    assert is_permission_satisfied(effective, "wallets.adjust") is True


def test_wildcard_satisfies_multiple_required_permissions():
    effective = {WILDCARD_PERMISSION}

    assert is_permission_satisfied(effective, ["a.read", "b.write", "c.delete"]) is True


def test_wildcard_alongside_other_keys_still_satisfies_any():
    effective = {WILDCARD_PERMISSION, "merchants.read"}

    assert is_permission_satisfied(effective, "anything.at.all") is True


# ---------------------------------------------------------------------------
# is_permission_satisfied — deny-by-default (Requirement 2.6)
# ---------------------------------------------------------------------------


def test_absent_permission_is_denied():
    effective = {"merchants.read", "invoices.read"}

    assert is_permission_satisfied(effective, "wallets.adjust") is False


def test_empty_effective_set_denies_non_empty_requirement():
    assert is_permission_satisfied(set(), "wallets.adjust") is False


def test_present_single_permission_is_granted():
    effective = {"wallets.adjust", "invoices.read"}

    assert is_permission_satisfied(effective, "wallets.adjust") is True


# ---------------------------------------------------------------------------
# is_permission_satisfied — conjunctive multi-permission checks
# ---------------------------------------------------------------------------


def test_all_required_permissions_present_is_granted():
    effective = {"a.read", "b.write", "c.delete"}

    assert is_permission_satisfied(effective, ["a.read", "b.write"]) is True


def test_any_missing_required_permission_is_denied():
    effective = {"a.read", "b.write"}

    # `c.delete` is missing -> conjunctive check fails.
    assert is_permission_satisfied(effective, ["a.read", "c.delete"]) is False


def test_empty_requirement_is_trivially_satisfied():
    # No permission gate -> satisfied even by an empty effective set.
    assert is_permission_satisfied(set(), []) is True


# ---------------------------------------------------------------------------
# Requirement 2.8 — custom roles resolve identically to predefined ones.
# Resolution inspects only permission keys, never role names or the
# `is_predefined` flag.
# ---------------------------------------------------------------------------


def test_custom_and_predefined_roles_resolve_identically():
    predefined = _make_role(
        "Finance", ["wallets.adjust", "invoices.read"], is_predefined=True
    )
    custom = _make_role(
        "Custom Finance", ["wallets.adjust", "invoices.read"], is_predefined=False
    )

    assert effective_permissions_from_roles(
        [predefined]
    ) == effective_permissions_from_roles([custom])


def test_custom_role_grants_authorization_by_permission_key():
    custom = _make_role("Bespoke", ["merchants.suspend"], is_predefined=False)

    effective = effective_permissions_from_roles([custom])

    # Authorization is driven purely by the key, regardless of role name.
    assert is_permission_satisfied(effective, "merchants.suspend") is True
    assert is_permission_satisfied(effective, "merchants.activate") is False


# ---------------------------------------------------------------------------
# DB-backed: AdminRBACService.load_effective_permissions / has_permissions
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def seeded_account(db_session) -> AdminAccount:
    """An account holding two roles whose permissions overlap on one key."""
    p_wallets = Permission(key="wallets.adjust")
    p_invoices = Permission(key="invoices.read")
    p_audit = Permission(key="audit.read")

    finance = Role(name=f"Finance-{uuid.uuid4().hex[:8]}", is_predefined=True)
    finance.permissions = [p_wallets, p_invoices]

    auditor = Role(name=f"Auditor-{uuid.uuid4().hex[:8]}", is_predefined=False)
    # `invoices.read` overlaps with Finance; union must dedupe it.
    auditor.permissions = [p_invoices, p_audit]

    account = AdminAccount(
        email=f"admin-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x" * 20,
        full_name="RBAC Subject",
    )
    account.roles = [finance, auditor]

    db_session.add(account)
    await db_session.commit()
    await db_session.refresh(account)
    return account


async def test_load_effective_permissions_unions_seeded_roles(db_session, seeded_account):
    service = AdminRBACService(db_session)

    effective = await service.load_effective_permissions(seeded_account.id)

    assert effective == {"wallets.adjust", "invoices.read", "audit.read"}


async def test_load_effective_permissions_unknown_account_is_empty(db_session):
    service = AdminRBACService(db_session)

    effective = await service.load_effective_permissions(uuid.uuid4())

    assert effective == set()


async def test_has_permissions_grants_when_present(db_session, seeded_account):
    service = AdminRBACService(db_session)

    assert await service.has_permissions(seeded_account.id, "wallets.adjust") is True
    assert (
        await service.has_permissions(
            seeded_account.id, ["invoices.read", "audit.read"]
        )
        is True
    )


async def test_has_permissions_denies_when_absent(db_session, seeded_account):
    service = AdminRBACService(db_session)

    assert await service.has_permissions(seeded_account.id, "merchants.suspend") is False
    # Conjunctive: one present, one absent -> denied.
    assert (
        await service.has_permissions(
            seeded_account.id, ["wallets.adjust", "merchants.suspend"]
        )
        is False
    )


async def test_account_with_no_roles_denies_by_default(db_session):
    account = AdminAccount(
        email=f"admin-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x" * 20,
        full_name="Roleless",
    )
    db_session.add(account)
    await db_session.commit()
    await db_session.refresh(account)

    service = AdminRBACService(db_session)

    assert await service.load_effective_permissions(account.id) == set()
    assert await service.has_permissions(account.id, "wallets.adjust") is False

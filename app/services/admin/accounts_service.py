"""Admin account management service (R3 — accounts).

The ``AdminAccountsService`` implements the account-management operations that
back the admin ``accounts`` router (wired in a later task):

  * **List** admin accounts (paginated) — each item exposes ``id``, ``email``,
    the names of its assigned roles, and its active status (Requirement 3.4).
  * **Deactivate** an account by setting ``is_active=False`` (Requirement 3.5),
    guarded so the **last remaining active Super Admin** can never be
    deactivated — the attempt is refused with an HTTP 409 conflict
    (Requirement 3.7).
  * **Reactivate** an account by setting ``is_active=True`` (Requirement 3.6).
  * **Assign roles** to an account, replacing its current role set with the
    requested roles (Requirements 2.5, 3.5/3.6 status flows).

This module is intentionally self-contained: it issues its own paginated query
rather than depending on the shared listing engine (``listing.py``), so it can
land independently of that task. Routers (and the ``audited(...)`` wrapper) are
implemented separately; this service only owns the persistence-level logic and
its invariants.
"""
from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.models.admin import AdminAccount, Role, admin_account_roles

#: Name of the seeded role that holds the wildcard permission. The
#: last-active-holder guard is keyed off this role name (Requirement 3.7).
SUPER_ADMIN_ROLE_NAME = "Super Admin"


@dataclass
class AccountListItem:
    """A single row in the admin-accounts listing (Requirement 3.4)."""

    id: uuid.UUID
    email: str
    role_names: list[str]
    is_active: bool


@dataclass
class AccountPage:
    """Pagination envelope mirroring the shared listing envelope shape."""

    items: list[AccountListItem]
    page: int
    page_size: int
    total: int
    total_pages: int
    has_next: bool
    next_page: int | None


@dataclass
class AccountListParams:
    """Inputs to :meth:`AdminAccountsService.list_accounts`.

    ``page`` is 1-based; ``page_size`` is clamped to ``ADMIN_MAX_PAGE_SIZE`` and
    defaults to ``ADMIN_DEFAULT_PAGE_SIZE`` when not provided.
    """

    page: int = 1
    page_size: int | None = None


class AdminAccountsService:
    """Account-management operations for admin accounts (R3)."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self._settings = get_settings()

    # -- Listing -----------------------------------------------------------

    async def list_accounts(self, params: AccountListParams) -> AccountPage:
        """Return a paginated page of admin accounts.

        Each item carries ``id``, ``email``, the names of its assigned roles,
        and its active status. Soft-deleted accounts (``deleted_at`` set) are
        excluded. Ordering is stable (by email) so pagination is deterministic.
        """
        page = params.page if params.page and params.page >= 1 else 1
        default_size = self._settings.ADMIN_DEFAULT_PAGE_SIZE
        max_size = self._settings.ADMIN_MAX_PAGE_SIZE
        page_size = params.page_size or default_size
        if page_size < 1:
            page_size = default_size
        page_size = min(page_size, max_size)

        base = select(AdminAccount).where(AdminAccount.deleted_at.is_(None))

        total = int(
            (
                await self.db.execute(
                    select(func.count()).select_from(base.subquery())
                )
            ).scalar()
            or 0
        )

        stmt = (
            base.options(selectinload(AdminAccount.roles))
            .order_by(AdminAccount.email)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        accounts = (await self.db.execute(stmt)).scalars().unique().all()

        items = [
            AccountListItem(
                id=account.id,
                email=account.email,
                role_names=sorted(role.name for role in account.roles),
                is_active=account.is_active,
            )
            for account in accounts
        ]

        total_pages = math.ceil(total / page_size) if total else 0
        has_next = page < total_pages
        return AccountPage(
            items=items,
            page=page,
            page_size=page_size,
            total=total,
            total_pages=total_pages,
            has_next=has_next,
            next_page=page + 1 if has_next else None,
        )

    # -- Status transitions ------------------------------------------------

    async def deactivate_account(self, account_id: uuid.UUID) -> AdminAccount:
        """Set ``is_active=False`` on the account (Requirement 3.5).

        Guards against removing the last remaining active Super Admin: if the
        target holds the Super Admin role and no other *active*, non-deleted
        account also holds it, the operation is refused with HTTP 409
        (Requirement 3.7). Deactivating an already-inactive account is a no-op
        that returns the account unchanged.
        """
        account = await self._get_account_or_404(account_id)

        if account.is_active and self._holds_super_admin(account):
            if not await self._other_active_super_admin_exists(account_id):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="cannot deactivate the last active Super Admin",
                )

        account.is_active = False
        await self.db.commit()
        await self.db.refresh(account)
        return account

    async def reactivate_account(self, account_id: uuid.UUID) -> AdminAccount:
        """Set ``is_active=True`` on the account (Requirement 3.6)."""
        account = await self._get_account_or_404(account_id)
        account.is_active = True
        await self.db.commit()
        await self.db.refresh(account)
        return account

    # -- Role assignment ---------------------------------------------------

    async def assign_roles(
        self,
        account_id: uuid.UUID,
        role_ids: list[uuid.UUID],
    ) -> AdminAccount:
        """Replace the account's assigned roles with ``role_ids`` (R2.5).

        The provided set fully replaces the account's current roles. Every id
        must reference an existing role; an unknown id is refused with HTTP 404.
        Duplicate ids in the input are de-duplicated. Assigning an empty list
        clears all roles (leaving the account with no effective permissions).
        """
        account = await self._get_account_or_404(account_id)

        unique_ids = list(dict.fromkeys(role_ids))
        if unique_ids:
            roles = (
                (
                    await self.db.execute(
                        select(Role).where(Role.id.in_(unique_ids))
                    )
                )
                .scalars()
                .unique()
                .all()
            )
            found_ids = {role.id for role in roles}
            missing = [rid for rid in unique_ids if rid not in found_ids]
            if missing:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"unknown role id(s): {', '.join(str(m) for m in missing)}",
                )
        else:
            roles = []

        account.roles = list(roles)
        await self.db.commit()
        await self.db.refresh(account)
        return account

    # -- Internal helpers --------------------------------------------------

    async def _get_account_or_404(self, account_id: uuid.UUID) -> AdminAccount:
        stmt = (
            select(AdminAccount)
            .where(
                AdminAccount.id == account_id,
                AdminAccount.deleted_at.is_(None),
            )
            .options(selectinload(AdminAccount.roles))
        )
        account = (await self.db.execute(stmt)).scalar_one_or_none()
        if account is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="admin account not found",
            )
        return account

    @staticmethod
    def _holds_super_admin(account: AdminAccount) -> bool:
        return any(role.name == SUPER_ADMIN_ROLE_NAME for role in account.roles)

    async def _other_active_super_admin_exists(
        self,
        exclude_account_id: uuid.UUID,
    ) -> bool:
        """Return True if some *other* active, non-deleted account is a Super Admin."""
        stmt = (
            select(func.count())
            .select_from(AdminAccount)
            .join(
                admin_account_roles,
                admin_account_roles.c.admin_account_id == AdminAccount.id,
            )
            .join(Role, Role.id == admin_account_roles.c.role_id)
            .where(
                Role.name == SUPER_ADMIN_ROLE_NAME,
                AdminAccount.id != exclude_account_id,
                AdminAccount.is_active.is_(True),
                AdminAccount.deleted_at.is_(None),
            )
        )
        count = int((await self.db.execute(stmt)).scalar() or 0)
        return count > 0

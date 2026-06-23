"""Multi-account list & switch service (R4).

The :class:`MultiAccountService` backs the two multi-account operations of the
admin API:

  * **List linked accounts** (R4.1, R4.2): return the accounts linked to the
    authenticated identity — each exposing identifier, email, associated role
    names, and active status. When nothing is linked beyond the authenticated
    identity, an **empty list** is returned rather than an error (R4.2).
  * **Switch** (R4.3–R4.6): switch to a linked account the caller is authorized
    to use and that is active, issuing a brand-new admin token pair scoped to
    the *target* account and *its* roles (R4.3). Errors never issue a token:
    unauthorized target → ``403`` (R4.4), non-existent target → ``404`` (R4.5),
    inactive target → ``409`` (R4.6).

----------------------------------------------------------------------------
Assumption — what "linked accounts" means (documented pragmatic interpretation)
----------------------------------------------------------------------------
The data model (``app/models/admin.py``) does **not** yet contain an explicit
account-linking table that records which admin identities belong to the same
person/operator. Until such a mechanism exists, this service adopts a single,
explicit definition of "linked":

    An account is *linked* to the authenticated identity if, and only if, it is
    the authenticated account itself (self).

Consequences of this interpretation, all consistent with Requirement 4:

  * :meth:`list_linked_accounts` returns the accounts linked **beyond** the
    authenticated identity, which is currently the empty list (R4.2).
  * :meth:`switch` authorizes a self-switch (re-mints a token pair for the
    caller's own roles) and refuses every other existing account with ``403``
    (R4.4); a non-existent target yields ``404`` (R4.5); a linked-but-inactive
    target yields ``409`` (R4.6).

The authorization decision is funnelled through the single explicit predicate
:meth:`_linked_account_ids`. When a real linking mechanism is added, only that
method needs to change — the ordered status-code logic in :meth:`switch`
remains correct.

Token issuance and refresh-session persistence mirror
:class:`app.services.admin.auth_service.AdminAuthService` (reusing
:func:`app.services.admin.auth_service.hash_refresh_token` and the
``create_admin_access_token`` / ``create_admin_refresh_token`` primitives from
:mod:`app.core.admin_security`).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.admin_security import (
    create_admin_access_token,
    create_admin_refresh_token,
)
from app.core.config import get_settings
from app.models.admin import AdminAccount, AdminRefreshToken
from app.schemas.admin.accounts import LinkedAccount
from app.schemas.admin.auth import TokenPair
from app.services.admin.auth_service import hash_refresh_token


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MultiAccountService:
    """Stateful per-request service bound to an :class:`AsyncSession`."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self._settings = get_settings()

    # -- public API ---------------------------------------------------------

    async def list_linked_accounts(self, actor: AdminAccount) -> list[LinkedAccount]:
        """Return the accounts linked to ``actor`` beyond its own identity (R4.1, R4.2).

        Each returned item carries the account's identifier, email, associated
        role names, and active status. With no account-linking mechanism in the
        data model yet (see the module docstring), this is currently always the
        empty list — which is the correct, non-error response demanded by R4.2.
        """
        linked_ids = self._linked_account_ids(actor) - {actor.id}
        if not linked_ids:
            return []

        stmt = (
            select(AdminAccount)
            .where(
                AdminAccount.id.in_(linked_ids),
                AdminAccount.deleted_at.is_(None),
            )
            .options(selectinload(AdminAccount.roles))
            .order_by(AdminAccount.email)
        )
        accounts = (await self.db.execute(stmt)).scalars().unique().all()
        return [self._to_linked_account(account) for account in accounts]

    async def switch(self, actor: AdminAccount, target_id: uuid.UUID) -> TokenPair:
        """Switch ``actor`` to ``target_id`` and issue a scoped token pair (R4.3–R4.6).

        Checks are applied in a fixed order so the correct status code wins and
        **no token is issued on any error**:

          1. Target does not exist (or is soft-deleted) → ``404`` (R4.5).
          2. Target exists but is not linked/authorized → ``403`` (R4.4).
          3. Target is linked but inactive → ``409`` (R4.6).
          4. Otherwise → issue a new token pair scoped to the target account and
             *its* roles (R4.3).
        """
        target = await self._load_account(target_id)
        if target is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="target account not found",
            )

        if target.id not in self._linked_account_ids(actor):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="not authorized to switch to this account",
            )

        if not target.is_active:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="target account is inactive",
            )

        return await self._issue_token_pair(target)

    # -- authorization model -----------------------------------------------

    def _linked_account_ids(self, actor: AdminAccount) -> set[uuid.UUID]:
        """Return the set of account ids ``actor`` is linked to (incl. self).

        This is the single, explicit authorization predicate for multi-account
        switching. Absent an account-linking table in the data model, the only
        linked account is the authenticated identity itself. Introducing a real
        linking mechanism later means changing only this method.
        """
        return {actor.id}

    # -- internal helpers ---------------------------------------------------

    async def _load_account(self, account_id: uuid.UUID) -> AdminAccount | None:
        """Load a non-deleted account (with roles) by id, or ``None``."""
        stmt = (
            select(AdminAccount)
            .where(
                AdminAccount.id == account_id,
                AdminAccount.deleted_at.is_(None),
            )
            .options(selectinload(AdminAccount.roles))
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    @staticmethod
    def _role_names(account: AdminAccount) -> list[str]:
        return [role.name for role in account.roles]

    @classmethod
    def _to_linked_account(cls, account: AdminAccount) -> LinkedAccount:
        return LinkedAccount(
            id=account.id,
            email=account.email,
            roles=sorted(cls._role_names(account)),
            is_active=account.is_active,
        )

    async def _issue_token_pair(self, account: AdminAccount) -> TokenPair:
        """Issue an access+refresh pair scoped to ``account`` and persist the session.

        Mirrors :meth:`AdminAuthService._issue_token_pair`: a fresh
        ``session_id`` is minted per pair, the access token carries the target
        account's role names (so the new token is scoped to the target), and the
        refresh token is persisted as an :class:`AdminRefreshToken` storing only
        a SHA-256 hash of the token.
        """
        session_id = uuid.uuid4()
        access_token = create_admin_access_token(
            str(account.id), roles=self._role_names(account)
        )
        refresh_token = create_admin_refresh_token(
            str(account.id), session_id=str(session_id)
        )

        expires_at = _now() + timedelta(
            days=self._settings.ADMIN_REFRESH_TOKEN_EXPIRE_DAYS
        )
        self.db.add(
            AdminRefreshToken(
                admin_account_id=account.id,
                session_id=session_id,
                token_hash=hash_refresh_token(refresh_token),
                expires_at=expires_at,
            )
        )
        await self.db.commit()
        return TokenPair(access_token=access_token, refresh_token=refresh_token)

"""Admin authentication service — login, refresh rotation, and logout (R1).

The ``AdminAuthService`` is the brand-new, fully separate admin login flow
(distinct from the consumer ``User`` auth). It:

- **Login** (R1.1, R1.2, R1.8): verifies the submitted credentials with
  bcrypt :func:`app.core.security.verify_password`, enforces a brute-force
  lockout using the windowed helpers in :mod:`app.core.admin_security`
  (persisting ``AdminAccount.failed_login_count`` / ``locked_until``), and on
  success issues an admin-audience access token carrying the account's role
  names plus a refresh token bound to a fresh ``session_id``. The refresh token
  is persisted as an :class:`AdminRefreshToken` row storing only a SHA-256
  *hash* of the token (never the raw token).
- **Refresh** (R1.3, R1.4): validates the presented refresh token's signature,
  audience, and type, matches it against a stored, non-revoked, non-expired
  session by hash, then rotates to a new token pair (revoking the old session).
- **Logout** (R1.5): revokes the refresh token's stored session.

Token hashing uses SHA-256 rather than bcrypt because refresh tokens are
already high-entropy JWTs (and exceed bcrypt's 72-byte input limit); a fast
cryptographic digest is the appropriate, collision-resistant choice for an
opaque-lookup session store.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.admin_security import (
    TokenError,
    clear_failed_attempts,
    create_admin_access_token,
    create_admin_refresh_token,
    decode_admin_token,
    is_locked_out,
    register_failed_attempt,
)
from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.security import verify_password
from app.models.admin import AdminAccount, AdminRefreshToken
from app.schemas.admin.auth import TokenPair

settings = get_settings()
log = get_logger("app.services.admin.auth")

# Generic message returned for any login failure so we never reveal whether an
# email exists or whether it was the password that was wrong.
_INVALID_CREDENTIALS = "invalid credentials"


def hash_refresh_token(token: str) -> str:
    """Return the SHA-256 hex digest used as the stored session lookup key.

    Refresh tokens are never persisted in raw form; only this digest is stored
    on :class:`AdminRefreshToken.token_hash`.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AdminAuthService:
    """Stateful per-request service bound to an :class:`AsyncSession`."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # -- internal helpers ---------------------------------------------------

    async def _load_account_by_email(self, email: str) -> AdminAccount | None:
        stmt = (
            select(AdminAccount)
            .where(AdminAccount.email == email.lower())
            .options(selectinload(AdminAccount.roles))
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def _load_active_account(self, account_id: uuid.UUID) -> AdminAccount | None:
        stmt = (
            select(AdminAccount)
            .where(AdminAccount.id == account_id)
            .options(selectinload(AdminAccount.roles))
        )
        result = await self.db.execute(stmt)
        account = result.scalar_one_or_none()
        if account is None or not account.is_active or account.deleted_at is not None:
            return None
        return account

    @staticmethod
    def _role_names(account: AdminAccount) -> list[str]:
        return [role.name for role in account.roles]

    async def _issue_token_pair(self, account: AdminAccount) -> TokenPair:
        """Issue an access+refresh token pair and persist the refresh session.

        A new ``session_id`` is generated for each pair so sessions can be
        revoked (logout) and rotated (refresh) independently.
        """
        session_id = uuid.uuid4()
        access_token = create_admin_access_token(
            str(account.id), roles=self._role_names(account)
        )
        refresh_token = create_admin_refresh_token(
            str(account.id), session_id=str(session_id)
        )

        expires_at = _now() + timedelta(
            days=settings.ADMIN_REFRESH_TOKEN_EXPIRE_DAYS
        )
        self.db.add(
            AdminRefreshToken(
                admin_account_id=account.id,
                session_id=session_id,
                token_hash=hash_refresh_token(refresh_token),
                expires_at=expires_at,
            )
        )
        await self.db.flush()
        return TokenPair(access_token=access_token, refresh_token=refresh_token)

    # -- public API ---------------------------------------------------------

    async def login(self, email: str, password: str) -> TokenPair:
        """Authenticate credentials and issue a token pair (R1.1, R1.2, R1.8).

        - While the account is within its lockout window → ``423 Locked``.
        - On invalid credentials → ``401`` and the windowed failed-attempt
          counter is incremented (locking the account once it reaches the
          configured maximum).
        - On success → reset lockout state and return a fresh token pair.
        """
        now = _now()
        account = await self._load_account_by_email(email)

        # Reject while locked out, before any credential check.
        if account is not None and is_locked_out(account.locked_until, now=now):
            raise HTTPException(
                status_code=status.HTTP_423_LOCKED,
                detail="account temporarily locked due to failed login attempts",
            )

        credentials_ok = (
            account is not None
            and account.hashed_password is not None
            and account.is_active
            and account.deleted_at is None
            and verify_password(password, account.hashed_password)
        )

        if not credentials_ok:
            if account is not None:
                # `updated_at` is touched on every row mutation, so it doubles
                # as the timestamp of the previous failed attempt for the
                # windowed lockout evaluation.
                new_count, _last, locked_until = register_failed_attempt(
                    account.failed_login_count,
                    account.updated_at,
                    now=now,
                )
                account.failed_login_count = new_count
                if locked_until is not None:
                    account.locked_until = locked_until
                await self.db.commit()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=_INVALID_CREDENTIALS,
            )

        # Success: clear lockout state and issue tokens.
        failed_count, last_failed_at, locked_until = clear_failed_attempts()
        account.failed_login_count = failed_count
        account.locked_until = locked_until
        pair = await self._issue_token_pair(account)
        await self.db.commit()
        return pair

    async def refresh(self, refresh_token: str) -> TokenPair:
        """Validate and rotate a refresh token into a new pair (R1.3, R1.4).

        The token must decode under the admin audience as a ``refresh`` token
        and match a stored session that is neither revoked nor expired. The old
        session is revoked as part of rotation; expired/revoked/unknown tokens
        respond ``401``.
        """
        try:
            claims = decode_admin_token(refresh_token, "refresh")
        except TokenError as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e)
            )

        token_hash = hash_refresh_token(refresh_token)
        stmt = select(AdminRefreshToken).where(
            AdminRefreshToken.token_hash == token_hash
        )
        result = await self.db.execute(stmt)
        stored = result.scalar_one_or_none()

        now = _now()
        if stored is None or stored.revoked:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="refresh token revoked or unknown",
            )

        expires_at = stored.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= now:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="refresh token expired",
            )

        # Defense in depth: the embedded session id must match the stored row.
        if claims.session_id and str(stored.session_id) != claims.session_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="refresh token session mismatch",
            )

        account = await self._load_active_account(stored.admin_account_id)
        if account is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="admin account inactive",
            )

        # Rotate: revoke the presented session and mint a new pair.
        stored.revoked = True
        pair = await self._issue_token_pair(account)
        await self.db.commit()
        return pair

    async def logout(self, refresh_token: str) -> bool:
        """Revoke the refresh token's session (R1.5).

        Idempotent: revoking an unknown or already-revoked token still reports
        success, since the desired end state (the token can no longer be used)
        holds either way.
        """
        token_hash = hash_refresh_token(refresh_token)
        stmt = select(AdminRefreshToken).where(
            AdminRefreshToken.token_hash == token_hash
        )
        result = await self.db.execute(stmt)
        stored = result.scalar_one_or_none()
        if stored is not None and not stored.revoked:
            stored.revoked = True
            await self.db.commit()
        return True

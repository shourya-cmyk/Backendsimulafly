"""Admin invitation service — create & activation (R3.1, R3.2, R3.3).

The ``AdminInvitationsService`` owns the persistence-level logic behind the two
invitation endpoints (the router and the ``audited(...)`` wrapper are
implemented separately):

- **Create** (Requirement 3.1): from ``{email, role_ids[]}`` it provisions a
  *pending* :class:`AdminAccount` (no password, ``is_active=False``) together
  with an :class:`AdminInvitation`. A high-entropy invitation token is
  generated; the **raw** token is returned to the caller exactly once, while
  only its SHA-256 hash is persisted on ``AdminInvitation.token_hash``. The
  invitation expires after at most 7 days. Inviting an email that already
  belongs to an *active* account is refused with HTTP 409; an empty or
  unknown/invalid ``role_ids`` set is refused with HTTP 422.

- **Activate** (Requirements 3.2, 3.3): from ``{token, password}`` it validates
  the invitation is *pending*, unexpired, and unused, then sets the account's
  password (bcrypt), activates it (``is_active=True``), assigns exactly the
  invited roles, and marks the invitation ``accepted``. An expired or
  already-used (or unknown) token is refused with HTTP 400.

Token hashing uses SHA-256 (mirroring ``auth_service.hash_refresh_token``):
invitation tokens are high-entropy random strings, so a fast cryptographic
digest is the appropriate, collision-resistant lookup key.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.security import hash_password
from app.models.admin import (
    AdminAccount,
    AdminInvitation,
    AdminInvitationStatus,
    Role,
)
from app.schemas.admin.invitations import (
    InvitationActivateResponse,
    InvitationCreateResponse,
)

settings = get_settings()
log = get_logger("app.services.admin.invitations")

#: Hard upper bound on an invitation's lifetime (Requirement 3.1: ``≤ 7 days``).
INVITATION_EXPIRY_DAYS = 7

#: Number of random bytes behind the raw invitation token.
_TOKEN_BYTES = 32


def hash_invitation_token(token: str) -> str:
    """Return the SHA-256 hex digest stored on ``AdminInvitation.token_hash``.

    The raw token is never persisted; only this digest is, so a stolen database
    row cannot be replayed to activate an account.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(dt: datetime) -> datetime:
    """Treat a naive timestamp (e.g. from a tz-less DB) as UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class AdminInvitationsService:
    """Invitation create & activation operations (R3)."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # -- Create ------------------------------------------------------------

    async def create_invitation(
        self,
        email: str,
        role_ids: list[uuid.UUID],
        *,
        created_by: uuid.UUID | None = None,
    ) -> InvitationCreateResponse:
        """Create a pending account + invitation and return the raw token (R3.1).

        - Rejects an empty ``role_ids`` set with HTTP 422.
        - Rejects ``role_ids`` referencing roles that do not exist with HTTP 422.
        - Rejects a duplicate email that already belongs to an *active*,
          non-deleted account with HTTP 409.
        - Reuses an existing *pending* (inactive) account for the same email so
          re-inviting does not violate the unique-email constraint.
        """
        normalized_email = email.strip().lower()

        # Validate role_ids: non-empty and every id must resolve to a real role.
        unique_role_ids = list(dict.fromkeys(role_ids))
        if not unique_role_ids:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="role_ids must contain at least one role",
            )
        roles = (
            (
                await self.db.execute(
                    select(Role).where(Role.id.in_(unique_role_ids))
                )
            )
            .scalars()
            .unique()
            .all()
        )
        found_ids = {role.id for role in roles}
        missing = [rid for rid in unique_role_ids if rid not in found_ids]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"unknown role id(s): {', '.join(str(m) for m in missing)}",
            )

        # Duplicate-email handling.
        existing = (
            await self.db.execute(
                select(AdminAccount).where(
                    AdminAccount.email == normalized_email,
                    AdminAccount.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()

        if existing is not None and existing.is_active:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="an active admin account with this email already exists",
            )

        if existing is not None:
            # Re-invite an as-yet-unactivated (pending) account.
            account = existing
        else:
            account = AdminAccount(
                email=normalized_email,
                hashed_password=None,
                is_active=False,
            )
            self.db.add(account)
            await self.db.flush()  # assign account.id

        # Generate the raw token; persist only its hash.
        raw_token = secrets.token_urlsafe(_TOKEN_BYTES)
        expires_at = _now() + timedelta(days=INVITATION_EXPIRY_DAYS)

        invitation = AdminInvitation(
            email=normalized_email,
            token_hash=hash_invitation_token(raw_token),
            role_ids=[str(rid) for rid in unique_role_ids],
            status=AdminInvitationStatus.PENDING.value,
            expires_at=expires_at,
            created_by=created_by,
        )
        self.db.add(invitation)
        await self.db.commit()
        await self.db.refresh(invitation)
        await self.db.refresh(account)

        return InvitationCreateResponse(
            invitation_id=invitation.id,
            account_id=account.id,
            email=normalized_email,
            role_ids=unique_role_ids,
            token=raw_token,
            status=invitation.status,
            expires_at=_as_aware(invitation.expires_at),
        )

    # -- Activate ----------------------------------------------------------

    async def activate_invitation(
        self,
        token: str,
        password: str,
    ) -> InvitationActivateResponse:
        """Activate an account from a valid invitation token (R3.2, R3.3).

        Validates that the invitation exists, is ``pending``, unexpired, and
        unused; then sets the account password, activates it, assigns exactly
        the invited roles, and marks the invitation ``accepted``. An unknown,
        expired, or already-used token is refused with HTTP 400.
        """
        token_hash = hash_invitation_token(token)
        invitation = (
            await self.db.execute(
                select(AdminInvitation).where(
                    AdminInvitation.token_hash == token_hash
                )
            )
        ).scalar_one_or_none()

        if invitation is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="invalid or unknown invitation token",
            )

        # Must be pending and unused.
        if (
            invitation.status != AdminInvitationStatus.PENDING.value
            or invitation.accepted_at is not None
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="invitation has already been used",
            )

        # Must be unexpired.
        if _as_aware(invitation.expires_at) <= _now():
            invitation.status = AdminInvitationStatus.EXPIRED.value
            await self.db.commit()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="invitation has expired",
            )

        # Load the pending account this invitation was issued for.
        account = (
            await self.db.execute(
                select(AdminAccount)
                .where(
                    AdminAccount.email == invitation.email,
                    AdminAccount.deleted_at.is_(None),
                )
                .options(selectinload(AdminAccount.roles))
            )
        ).scalar_one_or_none()

        if account is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="invitation account no longer exists",
            )

        # Resolve invited roles (ids were validated at create time).
        role_uuids = [uuid.UUID(str(rid)) for rid in (invitation.role_ids or [])]
        roles = (
            (
                await self.db.execute(
                    select(Role).where(Role.id.in_(role_uuids))
                )
            )
            .scalars()
            .unique()
            .all()
        ) if role_uuids else []

        # Activate the account and assign exactly the invited roles.
        account.hashed_password = hash_password(password)
        account.is_active = True
        account.roles = list(roles)

        # Mark the invitation accepted (single-use).
        invitation.status = AdminInvitationStatus.ACCEPTED.value
        invitation.accepted_at = _now()

        await self.db.commit()
        await self.db.refresh(account)

        return InvitationActivateResponse(
            account_id=account.id,
            email=account.email,
            is_active=account.is_active,
            role_ids=[role.id for role in account.roles],
        )

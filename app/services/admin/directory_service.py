"""Consumer user directory service (Requirement 7).

``DirectoryService`` backs the admin ``users.py`` router. It reuses the shared
listing engine (:func:`app.services.admin.listing.paginate`) over the existing
:class:`app.models.user.User` model to provide:

* **Listing** — paginated browse with case-insensitive search across name/email
  (R7.2), conjunctive equality filters (R7.3), whitelisted sort (R7.4), and the
  uniform pagination envelope (R7.1).
* **Detail** — a single user's record including design profile, credit balance,
  and referral attribution (R7.5); a missing identifier raises HTTP 404 (R7.6).
* **Status transitions** — suspend (``is_active=False``, R7.7) and reactivate
  (``is_active=True``, R7.8). Auditing is performed at the router boundary via
  the ``audited(...)`` dependency.
* **Attribution** — referral source and acquisition attribution for a set of
  users (R7.9). A user's ``referred_by_code`` is resolved against merchant
  referral codes to classify the acquisition as ``merchant`` (with the owning
  merchant surfaced) or ``organic``.

The ``User`` model has no soft-delete column, so listings span all rows.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.merchant import Merchant
from app.models.user import User
from app.schemas.admin.users import (
    ACQUISITION_MERCHANT,
    ACQUISITION_ORGANIC,
    UserAttribution,
)
from app.services.admin.listing import ListParams, Page, paginate

#: Columns the user directory may be sorted by (R7.4). Keys are the public field
#: names accepted on the ``sort`` query param; an unlisted field yields HTTP 422.
USER_SORTABLE: dict[str, object] = {
    "full_name": User.full_name,
    "email": User.email,
    "is_active": User.is_active,
    "credit_balance": User.credit_balance,
    "created_at": User.created_at,
}

#: Columns OR-matched (ILIKE) against a search term (R7.2).
USER_SEARCHABLE = (User.full_name, User.email)


@dataclass(frozen=True)
class _MerchantRef:
    """Lightweight merchant reference used to attribute a referral code."""

    id: uuid.UUID
    name: str


class DirectoryService:
    """User-directory operations backing the admin ``users`` router (R7)."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # -- Listing -----------------------------------------------------------

    async def list_users(self, params: ListParams) -> Page:
        """Return a paginated page of users (R7.1–R7.4).

        Search matches name/email (R7.2), filters are conjunctive (R7.3), and
        sort is restricted to :data:`USER_SORTABLE` (R7.4); an unsupported sort
        field raises HTTP 422 from the listing engine. ``Page.items`` are
        :class:`User` ORM instances.
        """
        return await paginate(
            self.db,
            select(User),
            params=params,
            sortable=USER_SORTABLE,
            searchable=USER_SEARCHABLE,
        )

    # -- Detail ------------------------------------------------------------

    async def get_user(self, user_id: uuid.UUID) -> User:
        """Return a single user by id, or raise HTTP 404 (R7.5, R7.6)."""
        user = await self.db.get(User, user_id)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="user not found",
            )
        return user

    # -- Status transitions ------------------------------------------------

    async def suspend_user(self, user_id: uuid.UUID) -> User:
        """Set ``is_active=False`` on the user (R7.7).

        Raises HTTP 404 when the user does not exist. Suspending an already
        inactive user is an idempotent no-op.
        """
        user = await self.get_user(user_id)
        user.is_active = False
        await self.db.commit()
        await self.db.refresh(user)
        return user

    async def reactivate_user(self, user_id: uuid.UUID) -> User:
        """Set ``is_active=True`` on the user (R7.8).

        Raises HTTP 404 when the user does not exist. Reactivating an already
        active user is an idempotent no-op.
        """
        user = await self.get_user(user_id)
        user.is_active = True
        await self.db.commit()
        await self.db.refresh(user)
        return user

    # -- Attribution -------------------------------------------------------

    async def resolve_attributions(
        self, users: list[User]
    ) -> dict[uuid.UUID, UserAttribution]:
        """Resolve referral source & acquisition attribution per user (R7.9).

        Batches a single lookup of merchants whose ``referral_code`` matches any
        of the supplied users' ``referred_by_code`` values. A code owned by a
        merchant is attributed to that merchant; any other (or absent) code is
        classified as ``organic``.
        """
        codes = {u.referred_by_code for u in users if u.referred_by_code}
        merchant_by_code: dict[str, _MerchantRef] = {}
        if codes:
            rows = (
                await self.db.execute(
                    select(Merchant).where(Merchant.referral_code.in_(codes))
                )
            ).scalars().all()
            merchant_by_code = {
                m.referral_code: _MerchantRef(id=m.id, name=m.display_name)
                for m in rows
            }

        attributions: dict[uuid.UUID, UserAttribution] = {}
        for user in users:
            code = user.referred_by_code
            merchant = merchant_by_code.get(code) if code else None
            if merchant is not None:
                attributions[user.id] = UserAttribution(
                    referred_by_code=code,
                    acquisition_source=ACQUISITION_MERCHANT,
                    referring_merchant_id=merchant.id,
                    referring_merchant_name=merchant.name,
                )
            else:
                attributions[user.id] = UserAttribution(
                    referred_by_code=code,
                    acquisition_source=ACQUISITION_ORGANIC,
                )
        return attributions

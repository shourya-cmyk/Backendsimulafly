"""Redeem-code admin service — batch generation, listing, deactivation (R15).

Backs ``app/routers/admin/redeem_codes.py``. This service operates over the
:class:`app.models.redeem_code.RedeemCode` table.

* :meth:`RedeemCodeService.generate` — create N unique codes sharing one
  ``value``/``expiry`` and a common ``batch_id`` (R15.1). Quantity below one or
  a non-positive value is rejected with HTTP 422 (R15.5) — the request schema
  enforces this, and the service re-validates defensively. Uniqueness is
  guaranteed by the table's ``UNIQUE`` constraint on ``code`` combined with a
  retry-on-collision loop: each code is a high-entropy
  :func:`secrets.token_urlsafe` token, and any :class:`IntegrityError` from a
  collision triggers regeneration of the offending row (R15.6). The router
  records the audit entry (R15.1).
* :meth:`RedeemCodeService.list_codes` — paginated listing including code,
  value, status, expiry, and redemption details (R15.2); optional filter by
  redemption status (R15.3) via the shared listing engine.
* :meth:`RedeemCodeService.deactivate` — mark a not-yet-redeemed code inactive
  (R15.4). A redeemed code cannot be deactivated (HTTP 409); an unknown code
  yields HTTP 404. The router records the audit entry (R15.4).
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.core.logging import get_logger
from app.models.redeem_code import RedeemCode, RedeemCodeStatus
from app.services.admin.listing import ListParams, Page, paginate

log = get_logger("app.services.admin.redeem_service")

#: Number of random bytes used per generated code token. ``token_urlsafe(n)``
#: yields a ~``ceil(n*4/3)``-character base64url string; 16 bytes → ~22 chars of
#: high-entropy text, comfortably unique and within the model's 64-char column.
_CODE_TOKEN_BYTES = 16

#: Max attempts to re-generate a single code on a UNIQUE-constraint collision
#: before giving up (R15.6). Collisions are astronomically unlikely at this
#: entropy, so this guards only against pathological cases.
_MAX_COLLISION_RETRIES = 5

#: Whitelisted sortable columns for the redeem-code directory (R15 / R20.4).
_SORTABLE: dict[str, ColumnElement] = {
    "value": RedeemCode.value,
    "status": RedeemCode.status,
    "expires_at": RedeemCode.expires_at,
    "redeemed_at": RedeemCode.redeemed_at,
    "created_at": RedeemCode.created_at,
}

#: Columns searched (ILIKE) by a free-text term (R20.5).
_SEARCHABLE: tuple[ColumnElement, ...] = (RedeemCode.code,)


def _new_code() -> str:
    """Return a fresh high-entropy, URL-safe redeem-code string."""
    return secrets.token_urlsafe(_CODE_TOKEN_BYTES)


class RedeemCodeService:
    """Generate / list / deactivate operations over the ``RedeemCode`` table."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def generate(
        self,
        *,
        value: Decimal,
        quantity: int,
        expiry: datetime | None,
    ) -> tuple[uuid.UUID, list[RedeemCode]]:
        """Create ``quantity`` unique active codes sharing ``value``/``expiry``.

        All codes in the call share one generated ``batch_id`` so the batch can
        be traced. Each code string is a high-entropy token; the table's
        ``UNIQUE`` constraint on ``code`` is the source of truth for uniqueness,
        and any collision raised as an :class:`IntegrityError` is retried with a
        freshly generated token (R15.6).

        Returns the ``batch_id`` and the list of created codes. Raises HTTP 422
        when ``quantity < 1`` or ``value <= 0`` (R15.5) — a defensive mirror of
        the request schema's validation.
        """
        if quantity < 1:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="quantity must be at least 1",
            )
        if value <= 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="value must be positive",
            )

        batch_id = uuid.uuid4()
        created: list[RedeemCode] = []

        for _ in range(quantity):
            code = await self._insert_unique_code(
                value=value, expiry=expiry, batch_id=batch_id
            )
            created.append(code)

        await self.db.commit()
        for code in created:
            await self.db.refresh(code)

        log.info(
            "admin_redeem_codes_generated",
            batch_id=str(batch_id),
            quantity=quantity,
            value=str(value),
        )
        return batch_id, created

    async def _insert_unique_code(
        self,
        *,
        value: Decimal,
        expiry: datetime | None,
        batch_id: uuid.UUID,
    ) -> RedeemCode:
        """Insert a single code, retrying on a UNIQUE collision (R15.6)."""
        last_error: IntegrityError | None = None
        for _ in range(_MAX_COLLISION_RETRIES):
            entry = RedeemCode(
                code=_new_code(),
                value=value,
                currency="INR",
                status=RedeemCodeStatus.ACTIVE.value,
                expires_at=expiry,
                batch_id=batch_id,
            )
            self.db.add(entry)
            try:
                await self.db.flush()
            except IntegrityError as exc:
                # Collision on the UNIQUE(code) constraint — roll the failed row
                # back and retry with a fresh token.
                last_error = exc
                await self.db.rollback()
                continue
            return entry

        # Exhausted retries (practically impossible at this entropy).
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="failed to generate a unique redeem code",
        ) from last_error

    async def list_codes(
        self,
        *,
        page: int = 1,
        page_size: int | None = None,
        sort: str | None = None,
        search: str | None = None,
        status_filter: str | None = None,
    ) -> Page:
        """Return a page of redeem codes (R15.2), optionally filtered by status (R15.3).

        Ordering defaults to newest-first; an explicit ``sort`` over a
        whitelisted field overrides it. The ``status_filter`` is a conjunctive
        equality filter applied through the shared listing engine.
        """
        params = ListParams(
            page=page,
            page_size=page_size if page_size is not None else ListParams().page_size,
            sort=sort or "-created_at",
            search=search,
            filters={"status": status_filter} if status_filter else {},
        )
        return await paginate(
            self.db,
            select(RedeemCode),
            params=params,
            sortable=_SORTABLE,
            searchable=_SEARCHABLE,
        )

    async def deactivate(self, code_id: uuid.UUID) -> RedeemCode:
        """Mark a not-yet-redeemed code inactive (R15.4).

        Raises HTTP 404 when the code id is unknown and HTTP 409 when the code
        has already been redeemed (a redeemed code cannot be deactivated). On
        success the code's status becomes ``inactive`` and the row is returned;
        the router records the audit entry.
        """
        code = await self._get_code_or_404(code_id)

        if code.status == RedeemCodeStatus.REDEEMED.value or code.redeemed_at is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="cannot deactivate a redeemed code",
            )

        code.status = RedeemCodeStatus.INACTIVE.value
        await self.db.commit()
        await self.db.refresh(code)

        log.info("admin_redeem_code_deactivated", code_id=str(code.id))
        return code

    # -- Internal helpers --------------------------------------------------

    async def _get_code_or_404(self, code_id: uuid.UUID) -> RedeemCode:
        code = (
            await self.db.execute(select(RedeemCode).where(RedeemCode.id == code_id))
        ).scalar_one_or_none()
        if code is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="redeem code not found",
            )
        return code

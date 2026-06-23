"""Merchant directory service — list, detail, and status actions (Requirement 8).

Backs ``app/routers/admin/merchants.py``. This module is intentionally separate
from the (concurrently developed) ``directory_service.py`` so the merchant
directory can be implemented without parallel-edit conflicts. It reuses the
shared listing engine (:func:`app.services.admin.listing.paginate`) for the
paginated/searchable/filterable/sortable listing (R8.1, R8.2, R8.3) and the
existing :class:`app.models.merchant.Merchant`, :class:`MerchantMember`, and
:class:`app.models.wallet.Wallet` models without modifying consumer/merchant
behavior.

Operations:

* :meth:`MerchantDirectoryService.list_merchants` — paginated listing with
  display/legal-name search, status/KYC filters, and whitelisted sort.
* :meth:`MerchantDirectoryService.get_merchant` — detail record including
  members (with the linked user's email/name) and a wallet summary; ``404``
  when the merchant id does not exist (R8.7).
* :meth:`MerchantDirectoryService.set_status` — set the merchant's status,
  used by the suspend (``suspended``) and activate (``active``) endpoints
  (R8.5, R8.6); ``404`` when missing.
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.elements import ColumnElement

from app.models.merchant import Merchant, MerchantMember, MerchantStatus
from app.models.wallet import Wallet
from app.services.admin.listing import ListParams, Page, paginate

#: Whitelisted sortable columns for the merchant directory (R8 / R20.4).
_SORTABLE: dict[str, ColumnElement] = {
    "display_name": Merchant.display_name,
    "legal_name": Merchant.legal_name,
    "status": Merchant.status,
    "is_kyc_completed": Merchant.is_kyc_completed,
    "created_at": Merchant.created_at,
}

#: Columns matched (ILIKE OR) by the free-text search term (R8.2).
_SEARCHABLE: tuple[ColumnElement, ...] = (
    Merchant.display_name,
    Merchant.legal_name,
)


class MerchantDirectoryService:
    """List/detail/status operations over :class:`Merchant`."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_merchants(
        self,
        *,
        page: int = 1,
        page_size: int | None = None,
        search: str | None = None,
        sort: str | None = None,
        status_filter: str | None = None,
        is_kyc_completed: bool | None = None,
    ) -> Page:
        """Return a page of merchants (R8.1, R8.2, R8.3).

        Search matches display name or legal name (R8.2). The ``status_filter``
        and ``is_kyc_completed`` filters are conjunctive (R8.3). Sorting is
        restricted to the whitelisted columns; an unsupported field yields
        HTTP 422 via the shared listing engine.
        """
        params = ListParams(
            page=page,
            page_size=page_size if page_size is not None else 1,
            search=search,
            sort=sort,
            filters={
                "status": status_filter,
                "is_kyc_completed": is_kyc_completed,
            },
        )
        # When the caller does not specify a page size, fall back to the
        # ListParams default rather than the placeholder above.
        if page_size is None:
            params.page_size = ListParams().page_size

        return await paginate(
            self.db,
            select(Merchant),
            params=params,
            sortable=_SORTABLE,
            searchable=_SEARCHABLE,
        )

    async def get_merchant(self, merchant_id: uuid.UUID) -> tuple[Merchant, Wallet | None]:
        """Load a merchant with its members and wallet summary (R8.4).

        Members are eagerly loaded together with their linked user so the detail
        projection can include each member's email and name. The merchant's
        wallet (one per merchant) is returned alongside, or ``None`` when the
        merchant has no wallet. Raises ``404`` when the merchant id is unknown
        (R8.7).
        """
        merchant = await self._get_or_404(merchant_id, load_members=True)
        wallet = (
            await self.db.execute(
                select(Wallet).where(Wallet.merchant_id == merchant_id)
            )
        ).scalar_one_or_none()
        return merchant, wallet

    async def set_status(self, merchant_id: uuid.UUID, new_status: str) -> Merchant:
        """Set a merchant's status and return the updated row (R8.5, R8.6).

        Raises ``404`` when the merchant id is unknown (R8.7). The caller (the
        router) is responsible for recording the audit entry via ``audited(...)``.
        """
        merchant = await self._get_or_404(merchant_id)
        merchant.status = new_status
        await self.db.commit()
        await self.db.refresh(merchant)
        return merchant

    async def suspend(self, merchant_id: uuid.UUID) -> Merchant:
        """Suspend a merchant (status → ``suspended``) (R8.5)."""
        return await self.set_status(merchant_id, MerchantStatus.SUSPENDED.value)

    async def activate(self, merchant_id: uuid.UUID) -> Merchant:
        """Activate a merchant (status → ``active``) (R8.6)."""
        return await self.set_status(merchant_id, MerchantStatus.ACTIVE.value)

    # -- Internal helpers --------------------------------------------------

    async def _get_or_404(
        self,
        merchant_id: uuid.UUID,
        *,
        load_members: bool = False,
    ) -> Merchant:
        stmt = select(Merchant).where(Merchant.id == merchant_id)
        if load_members:
            stmt = stmt.options(
                selectinload(Merchant.members).selectinload(MerchantMember.user)
            )
        merchant = (await self.db.execute(stmt)).scalar_one_or_none()
        if merchant is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="merchant not found",
            )
        return merchant

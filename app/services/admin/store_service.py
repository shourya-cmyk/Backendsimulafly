"""Store directory service (R9 — stores).

``StoreService`` backs the admin ``stores`` router. It reuses the shared
listing engine (:func:`app.services.admin.listing.paginate`) so search, the
owning-merchant filter, sort, soft-delete exclusion, and pagination behave
identically to every other admin directory:

  * **List** stores (paginated) — searchable by ``name`` (R9.2), filterable by
    owning ``merchant_id`` (R9.3), sortable by a whitelist, and excluding
    soft-deleted rows (R20/R22).
  * **Detail** — fetch a single store with its owning merchant eager-loaded;
    a missing identifier raises HTTP 404 (R9.4, R9.6).
  * **Change status** — persist a new :class:`StoreStatus` value (R9.5). The
    router wraps this in ``audited(...)`` so the action is recorded.

This service owns only persistence-level logic and its invariants; the router
handles permission gating, auditing, and schema mapping.
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.elements import ColumnElement

from app.models.store import Store, StoreStatus
from app.services.admin.listing import ListParams, Page, paginate


class StoreService:
    """List / detail / status operations for the store directory (R9)."""

    #: Whitelisted sortable (and string-filterable) fields → column expressions.
    SORTABLE: dict[str, ColumnElement] = {
        "name": Store.name,
        "status": Store.status,
        "created_at": Store.created_at,
        "merchant_id": Store.merchant_id,
    }

    #: Columns OR-matched (ILIKE) against the free-text search term (R9.2).
    SEARCHABLE: list[ColumnElement] = [Store.name]

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_stores(
        self,
        params: ListParams,
        *,
        merchant_id: uuid.UUID | None = None,
    ) -> Page:
        """Return a paginated page of stores.

        Search matches the store ``name`` (R9.2). When ``merchant_id`` is
        provided it is applied as a conjunctive filter so only stores owned by
        that merchant are returned (R9.3). Soft-deleted rows are excluded by
        default. The owning merchant is eager-loaded so the router can expose
        its display name without an extra query.
        """
        if merchant_id is not None:
            params.filters[Store.merchant_id] = merchant_id

        base_stmt = select(Store).options(selectinload(Store.merchant))
        return await paginate(
            self.db,
            base_stmt,
            params=params,
            sortable=self.SORTABLE,
            searchable=self.SEARCHABLE,
            soft_delete_col=Store.deleted_at,
        )

    async def get_store(self, store_id: uuid.UUID) -> Store:
        """Fetch a single non-deleted store with its merchant eager-loaded.

        Raises HTTP 404 when no matching store exists (R9.6).
        """
        stmt = (
            select(Store)
            .options(selectinload(Store.merchant))
            .where(Store.id == store_id, Store.deleted_at.is_(None))
        )
        store = (await self.db.execute(stmt)).scalar_one_or_none()
        if store is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="store not found",
            )
        return store

    async def change_status(
        self,
        store_id: uuid.UUID,
        new_status: StoreStatus,
    ) -> Store:
        """Persist a new status for the store and return it (R9.5).

        A missing store raises HTTP 404 (R9.6).
        """
        store = await self.get_store(store_id)
        store.status = new_status.value
        await self.db.commit()
        # Reload column attributes (the status response reads id/name/
        # merchant_id/status only; the merchant relationship is not accessed).
        await self.db.refresh(store)
        return store

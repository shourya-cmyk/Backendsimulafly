"""Invoice operations service (Requirement 16).

``InvoiceService`` backs the admin ``invoices`` router. It reuses the shared
listing engine (:func:`app.services.admin.listing.paginate`) over the new
:class:`app.models.invoice.Invoice` model to provide:

* **Listing** — paginated invoices with an optional conjunctive equality filter
  on ``status`` (R16.1, R16.2), whitelisted sort, and soft-delete exclusion.
* **Detail** — a single invoice including its ``line_items`` (R16.4); a missing
  identifier raises HTTP 404 (R16.6).
* **Mark paid** — persist ``status='paid'`` (and stamp ``paid_at``) (R16.5).
  Auditing is performed at the router boundary via ``audited(...)``.

The ``overdue`` marker is a **derived** classification (``status == 'unpaid'
AND due_date < now()``), not a stored status (R16.3); :meth:`is_overdue`
computes it for the response. ``Invoice`` has no eager-loaded ``Merchant``
display name, so the router resolves merchant names via
:meth:`resolve_merchant_names`, batching a single lookup.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status as http_status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.elements import ColumnElement

from app.models.invoice import Invoice, InvoiceStatus
from app.models.merchant import Merchant
from app.services.admin.listing import ListParams, Page, paginate


class InvoiceService:
    """List / detail / mark-paid operations for invoices (R16)."""

    #: Whitelisted sortable (and string-filterable) fields → column expressions.
    SORTABLE: dict[str, ColumnElement] = {
        "number": Invoice.number,
        "merchant_id": Invoice.merchant_id,
        "amount": Invoice.amount,
        "status": Invoice.status,
        "issue_date": Invoice.issue_date,
        "due_date": Invoice.due_date,
        "created_at": Invoice.created_at,
    }

    #: Invoice ``number`` is the natural free-text search target.
    SEARCHABLE: list[ColumnElement] = [Invoice.number]

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # -- Derived classification -------------------------------------------

    @staticmethod
    def is_overdue(invoice: Invoice, *, now: datetime | None = None) -> bool:
        """Return whether the invoice is overdue (R16.3).

        An invoice is overdue *if and only if* its status is ``unpaid`` and its
        ``due_date`` is in the past. This is a derived classification, never a
        stored status — marking the invoice paid therefore removes it.
        """
        reference = now or datetime.now(timezone.utc)
        return (
            invoice.status == InvoiceStatus.UNPAID.value
            and invoice.due_date < reference
        )

    # -- Listing -----------------------------------------------------------

    async def list_invoices(
        self,
        params: ListParams,
        *,
        invoice_status: str | None = None,
    ) -> Page:
        """Return a paginated page of invoices (R16.1, R16.2).

        ``invoice_status`` is applied as a conjunctive equality filter when
        provided. Soft-deleted rows are excluded.
        """
        if invoice_status is not None:
            params.filters[Invoice.status] = invoice_status

        base_stmt = select(Invoice)
        return await paginate(
            self.db,
            base_stmt,
            params=params,
            sortable=self.SORTABLE,
            searchable=self.SEARCHABLE,
            soft_delete_col=Invoice.deleted_at,
        )

    # -- Detail ------------------------------------------------------------

    async def get_invoice(self, invoice_id: uuid.UUID) -> Invoice:
        """Fetch a single non-deleted invoice with line items, or 404 (R16.4, R16.6)."""
        stmt = (
            select(Invoice)
            .where(Invoice.id == invoice_id, Invoice.deleted_at.is_(None))
            .options(selectinload(Invoice.line_items))
        )
        invoice = (await self.db.execute(stmt)).scalar_one_or_none()
        if invoice is None:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail="invoice not found",
            )
        return invoice

    # -- Mutations ---------------------------------------------------------

    async def mark_paid(self, invoice_id: uuid.UUID) -> Invoice:
        """Mark an invoice as paid (R16.5).

        Sets ``status='paid'`` and stamps ``paid_at``. A missing invoice raises
        HTTP 404 (R16.6). The router wraps this in ``audited(...)`` so the action
        is recorded.
        """
        invoice = await self.get_invoice(invoice_id)
        invoice.status = InvoiceStatus.PAID.value
        if invoice.paid_at is None:
            invoice.paid_at = datetime.now(timezone.utc)
        await self.db.commit()
        await self.db.refresh(invoice)
        return invoice

    # -- Name resolution ---------------------------------------------------

    async def resolve_merchant_names(
        self, invoices: list[Invoice]
    ) -> dict[uuid.UUID, str | None]:
        """Resolve merchant display names for a set of invoices.

        ``Invoice`` is not loaded with its ``Merchant`` display name, so this
        batches a single lookup keyed by ``merchant_id``. Missing references map
        to ``None`` rather than raising.
        """
        merchant_ids = {inv.merchant_id for inv in invoices}
        if not merchant_ids:
            return {}
        rows = (
            await self.db.execute(
                select(Merchant.id, Merchant.display_name).where(
                    Merchant.id.in_(merchant_ids)
                )
            )
        ).all()
        return {row[0]: row[1] for row in rows}

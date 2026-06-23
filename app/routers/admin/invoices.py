"""Admin invoices router — invoice listing/detail/mark-paid (R16).

Endpoints (all prefixed ``/admin``; mounted under ``/api/v1`` by the wiring
step). Invoices map onto the new :class:`app.models.invoice.Invoice` /
:class:`app.models.invoice.InvoiceLineItem` models.

| Method | Path                          | Permission         | Req              |
|--------|-------------------------------|--------------------|------------------|
| GET    | `/invoices`                   | `invoices.read`    | 16.1, 16.2, 16.3 |
| GET    | `/invoices/{id}`              | `invoices.read`    | 16.4, 16.6       |
| POST   | `/invoices/{id}/mark-paid`    | `invoices.manage`  | 16.5             |

Reads are gated by ``require_permission("invoices.read")`` and the mutating
mark-paid action by ``require_permission("invoices.manage")``. The list and
detail responses carry a derived ``overdue`` flag (``status == 'unpaid' AND
due_date < now``, R16.3) — not a stored status. The mark-paid route is wrapped
with ``audited(...)`` so the action writes one immutable audit entry (R16.5 /
R19.1).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.invoice import Invoice
from app.schemas.admin.invoices import (
    InvoiceDetail,
    InvoiceLineItemOut,
    InvoiceListItem,
    InvoiceListResponse,
    MarkPaidResponse,
)
from app.services.admin.audit_service import AuditContext, audited
from app.services.admin.invoice_service import InvoiceService
from app.services.admin.listing import ListParams
from app.utils.admin_dependencies import require_permission

router = APIRouter(prefix="/admin", tags=["admin-invoices"])

DBSession = Annotated[AsyncSession, Depends(get_db)]


def _to_list_item(
    invoice: Invoice,
    merchant_names: dict[uuid.UUID, str | None],
) -> InvoiceListItem:
    return InvoiceListItem(
        id=invoice.id,
        merchant_id=invoice.merchant_id,
        merchant_name=merchant_names.get(invoice.merchant_id),
        number=invoice.number,
        amount=invoice.amount,
        currency=invoice.currency,
        status=invoice.status,
        issue_date=invoice.issue_date,
        due_date=invoice.due_date,
        overdue=InvoiceService.is_overdue(invoice),
    )


@router.get(
    "/invoices",
    response_model=InvoiceListResponse,
    dependencies=[Depends(require_permission("invoices.read"))],
)
async def list_invoices(
    db: DBSession,
    sort: str | None = Query(default=None),
    invoice_status: str | None = Query(default=None, alias="status"),
    search: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int | None = Query(default=None, ge=1),
) -> InvoiceListResponse:
    """Paginated invoice directory: id, merchant, amount, status, dates (R16.1).

    Filterable by ``status`` (R16.2); each row carries a derived ``overdue``
    flag (R16.3). An unsupported sort field is rejected with HTTP 422.
    """
    params = ListParams(page=page, sort=sort, search=search)
    if page_size is not None:
        params.page_size = page_size

    service = InvoiceService(db)
    page_obj = await service.list_invoices(params, invoice_status=invoice_status)
    merchant_names = await service.resolve_merchant_names(page_obj.items)
    return InvoiceListResponse(
        items=[_to_list_item(inv, merchant_names) for inv in page_obj.items],
        page=page_obj.page,
        page_size=page_obj.page_size,
        total=page_obj.total,
        total_pages=page_obj.total_pages,
        has_next=page_obj.has_next,
        next_page=page_obj.next_page,
    )


@router.get(
    "/invoices/{invoice_id}",
    response_model=InvoiceDetail,
    dependencies=[Depends(require_permission("invoices.read"))],
)
async def get_invoice(
    invoice_id: uuid.UUID,
    db: DBSession,
) -> InvoiceDetail:
    """Return a single invoice's detail including line items (R16.4).

    A missing identifier yields HTTP 404 (R16.6).
    """
    service = InvoiceService(db)
    invoice = await service.get_invoice(invoice_id)
    merchant_names = await service.resolve_merchant_names([invoice])
    return InvoiceDetail(
        id=invoice.id,
        merchant_id=invoice.merchant_id,
        merchant_name=merchant_names.get(invoice.merchant_id),
        number=invoice.number,
        amount=invoice.amount,
        currency=invoice.currency,
        status=invoice.status,
        issue_date=invoice.issue_date,
        due_date=invoice.due_date,
        overdue=InvoiceService.is_overdue(invoice),
        line_items=[
            InvoiceLineItemOut.model_validate(item) for item in invoice.line_items
        ],
        paid_at=invoice.paid_at,
        created_at=invoice.created_at,
        updated_at=invoice.updated_at,
    )


@router.post(
    "/invoices/{invoice_id}/mark-paid",
    response_model=MarkPaidResponse,
    dependencies=[Depends(require_permission("invoices.manage"))],
)
async def mark_invoice_paid(
    invoice_id: uuid.UUID,
    db: DBSession,
    audit: Annotated[
        AuditContext, Depends(audited("invoices.mark_paid", "invoice"))
    ],
) -> MarkPaidResponse:
    """Mark an invoice as paid and record the action (R16.5).

    A missing identifier yields HTTP 404 (R16.6).
    """
    invoice = await InvoiceService(db).mark_paid(invoice_id)
    audit.set_target(invoice_id)
    audit.add_metadata(status=invoice.status)
    return MarkPaidResponse(
        id=invoice.id,
        status=invoice.status,
        paid_at=invoice.paid_at,
        overdue=InvoiceService.is_overdue(invoice),
    )

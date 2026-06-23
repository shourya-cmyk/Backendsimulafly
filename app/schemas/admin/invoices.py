"""Request/response schemas for admin Invoice operations (Requirement 16).

Invoices map onto the new :class:`app.models.invoice.Invoice` /
:class:`app.models.invoice.InvoiceLineItem` models. The listing reuses the
shared :class:`ListingEnvelope` so the Admin Panel consumes it with the same
pagination shape as every other directory.

The ``overdue`` marker is a **derived** classification computed at response time
(``status == 'unpaid' AND due_date < now()``); it is *not* a stored status on
the model (R16.3). Marking an invoice paid sets ``status='paid'`` which removes
the overdue classification (design Property 32).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.schemas.admin.listing import ListingEnvelope


class InvoiceLineItemOut(BaseModel):
    """A single invoice line item (R16.4)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    description: str
    quantity: int
    unit_amount: Decimal
    line_total: Decimal
    position: int


class InvoiceListItem(BaseModel):
    """A single row in the invoice listing (R16.1).

    Carries the invoice ``id`` (identifier), the ``merchant`` (``merchant_id``
    plus resolved ``merchant_name``), the human-readable invoice ``number``, the
    ``amount``/``currency``, the ``status``, the ``issue_date`` and ``due_date``,
    and the derived ``overdue`` flag (``status == 'unpaid' AND due_date < now``,
    R16.3).
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    merchant_id: uuid.UUID
    merchant_name: str | None = None
    number: str
    amount: Decimal
    currency: str
    status: str
    issue_date: datetime
    due_date: datetime
    overdue: bool


class InvoiceDetail(InvoiceListItem):
    """Detail record for a single invoice (R16.4).

    Extends the list item with the invoice ``line_items`` and the
    ``paid_at``/``created_at``/``updated_at`` timestamps.
    """

    line_items: list[InvoiceLineItemOut]
    paid_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


#: Pagination envelope for the invoice listing (R16.1, R16.2).
InvoiceListResponse = ListingEnvelope[InvoiceListItem]


class MarkPaidResponse(BaseModel):
    """Result of marking an invoice paid (R16.5)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: str
    paid_at: datetime | None = None
    overdue: bool

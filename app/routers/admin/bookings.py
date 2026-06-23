"""Admin bookings router — booking listing/detail/disputes/fulfillment (R11).

Endpoints (all prefixed ``/admin``; mounted under ``/api/v1`` by the wiring
step). Bookings map onto the existing :class:`app.models.lead.Order`.

| Method | Path                                  | Permission        | Req         |
|--------|---------------------------------------|-------------------|-------------|
| GET    | `/bookings`                           | `bookings.read`   | 11.1, 11.2  |
| GET    | `/bookings/disputes`                  | `bookings.read`   | 11.4        |
| GET    | `/bookings/fulfillment`               | `bookings.read`   | 11.6        |
| GET    | `/bookings/{id}`                      | `bookings.read`   | 11.3, 11.8  |
| POST   | `/bookings/{id}/dispute/resolve`      | `bookings.manage` | 11.5        |
| PATCH  | `/bookings/{id}/fulfillment`          | `bookings.manage` | 11.7        |

The static collection routes (`/bookings/disputes`, `/bookings/fulfillment`)
are declared **before** the parameterised `/bookings/{id}` route so the literal
paths are matched first and not shadowed by the ``{booking_id}`` capture.

Reads are gated by ``require_permission("bookings.read")`` and the mutating
dispute/fulfillment actions by ``require_permission("bookings.manage")``. Each
mutating route is wrapped with ``audited(...)`` so the action writes one
immutable audit entry (R11.5 / R11.7 / R19.1).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.lead import Order
from app.schemas.admin.bookings import (
    BookingDetail,
    BookingLineItem,
    BookingListItem,
    BookingListResponse,
    DisputeResolveRequest,
    DisputeResolveResponse,
    FulfillmentUpdateRequest,
    FulfillmentUpdateResponse,
)
from app.services.admin.audit_service import AuditContext, audited
from app.services.admin.booking_service import BookingService
from app.services.admin.listing import ListParams, Page
from app.utils.admin_dependencies import require_permission

router = APIRouter(prefix="/admin", tags=["admin-bookings"])

DBSession = Annotated[AsyncSession, Depends(get_db)]


def _to_list_item(
    order: Order,
    customer_names: dict[uuid.UUID, str | None],
    merchant_names: dict[uuid.UUID, str | None],
) -> BookingListItem:
    return BookingListItem(
        id=order.id,
        user_id=order.user_id,
        customer_name=customer_names.get(order.user_id),
        merchant_id=order.merchant_id,
        merchant_name=merchant_names.get(order.merchant_id),
        status=order.status,
        amount=order.total_estimated,
        dispute_status=order.dispute_status,
        fulfillment_status=order.fulfillment_status,
        created_at=order.created_at,
    )


def _line_items(order: Order) -> list[BookingLineItem]:
    raw = order.items or []
    return [BookingLineItem.model_validate(entry) for entry in raw]


async def _build_list_response(
    service: BookingService, page_obj: Page
) -> BookingListResponse:
    customer_names, merchant_names = await service.resolve_names(page_obj.items)
    return BookingListResponse(
        items=[
            _to_list_item(order, customer_names, merchant_names)
            for order in page_obj.items
        ],
        page=page_obj.page,
        page_size=page_obj.page_size,
        total=page_obj.total,
        total_pages=page_obj.total_pages,
        has_next=page_obj.has_next,
        next_page=page_obj.next_page,
    )


@router.get(
    "/bookings",
    response_model=BookingListResponse,
    dependencies=[Depends(require_permission("bookings.read"))],
)
async def list_bookings(
    db: DBSession,
    sort: str | None = Query(default=None),
    order_status: str | None = Query(default=None, alias="status"),
    merchant_id: uuid.UUID | None = Query(default=None),
    created_from: datetime | None = Query(default=None),
    created_to: datetime | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int | None = Query(default=None, ge=1),
) -> BookingListResponse:
    """Paginated booking directory: id, customer, merchant, status, amount, date.

    Filterable by ``status``, ``merchant_id``, and a created-at date range
    (R11.1, R11.2); an unsupported sort field is rejected with HTTP 422.
    """
    params = ListParams(page=page, sort=sort)
    if page_size is not None:
        params.page_size = page_size

    service = BookingService(db)
    page_obj = await service.list_bookings(
        params,
        order_status=order_status,
        merchant_id=merchant_id,
        created_from=created_from,
        created_to=created_to,
    )
    return await _build_list_response(service, page_obj)


# --- Static collection routes (declared BEFORE /bookings/{id}) ---


@router.get(
    "/bookings/disputes",
    response_model=BookingListResponse,
    dependencies=[Depends(require_permission("bookings.read"))],
)
async def list_disputes(
    db: DBSession,
    sort: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int | None = Query(default=None, ge=1),
) -> BookingListResponse:
    """Paginated list of bookings with an open dispute (R11.4)."""
    params = ListParams(page=page, sort=sort)
    if page_size is not None:
        params.page_size = page_size

    service = BookingService(db)
    page_obj = await service.list_disputes(params)
    return await _build_list_response(service, page_obj)


@router.get(
    "/bookings/fulfillment",
    response_model=BookingListResponse,
    dependencies=[Depends(require_permission("bookings.read"))],
)
async def list_fulfillment_queue(
    db: DBSession,
    sort: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int | None = Query(default=None, ge=1),
) -> BookingListResponse:
    """Paginated list of bookings pending fulfillment (R11.6)."""
    params = ListParams(page=page, sort=sort)
    if page_size is not None:
        params.page_size = page_size

    service = BookingService(db)
    page_obj = await service.list_fulfillment_queue(params)
    return await _build_list_response(service, page_obj)


# --- Parameterised routes (declared AFTER the static collection routes) ---


@router.get(
    "/bookings/{booking_id}",
    response_model=BookingDetail,
    dependencies=[Depends(require_permission("bookings.read"))],
)
async def get_booking(
    booking_id: uuid.UUID,
    db: DBSession,
) -> BookingDetail:
    """Return a single booking's detail incl. line items, fulfillment, dispute.

    A missing identifier yields HTTP 404 (R11.3, R11.8).
    """
    service = BookingService(db)
    order = await service.get_booking(booking_id)
    customer_names, merchant_names = await service.resolve_names([order])
    return BookingDetail(
        id=order.id,
        user_id=order.user_id,
        customer_name=customer_names.get(order.user_id),
        merchant_id=order.merchant_id,
        merchant_name=merchant_names.get(order.merchant_id),
        status=order.status,
        amount=order.total_estimated,
        dispute_status=order.dispute_status,
        fulfillment_status=order.fulfillment_status,
        created_at=order.created_at,
        lead_id=order.lead_id,
        line_items=_line_items(order),
        delivery_address=order.delivery_address or {},
        dispute_reason=order.dispute_reason,
        dispute_resolution=order.dispute_resolution,
        merchant_notes=order.merchant_notes,
        completed_at=order.completed_at,
        updated_at=order.updated_at,
    )


@router.post(
    "/bookings/{booking_id}/dispute/resolve",
    response_model=DisputeResolveResponse,
    dependencies=[Depends(require_permission("bookings.manage"))],
)
async def resolve_dispute(
    booking_id: uuid.UUID,
    payload: DisputeResolveRequest,
    db: DBSession,
    audit: Annotated[
        AuditContext, Depends(audited("bookings.dispute.resolve", "booking"))
    ],
) -> DisputeResolveResponse:
    """Resolve a booking dispute and record the action (R11.5)."""
    order = await BookingService(db).resolve_dispute(
        booking_id,
        resolution=payload.resolution,
        new_status=payload.status,
    )
    audit.set_target(booking_id)
    audit.add_metadata(
        dispute_status=order.dispute_status,
        resolution=order.dispute_resolution,
    )
    return DisputeResolveResponse(
        id=order.id,
        dispute_status=order.dispute_status,
        dispute_resolution=order.dispute_resolution,
    )


@router.patch(
    "/bookings/{booking_id}/fulfillment",
    response_model=FulfillmentUpdateResponse,
    dependencies=[Depends(require_permission("bookings.manage"))],
)
async def update_fulfillment(
    booking_id: uuid.UUID,
    payload: FulfillmentUpdateRequest,
    db: DBSession,
    audit: Annotated[
        AuditContext, Depends(audited("bookings.fulfillment.update", "booking"))
    ],
) -> FulfillmentUpdateResponse:
    """Persist a new fulfillment status and record the action (R11.7)."""
    order = await BookingService(db).update_fulfillment(booking_id, payload.status)
    audit.set_target(booking_id)
    audit.add_metadata(fulfillment_status=order.fulfillment_status)
    return FulfillmentUpdateResponse(
        id=order.id,
        fulfillment_status=order.fulfillment_status,
    )

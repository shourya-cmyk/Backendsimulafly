"""Merchant-facing support ticket router.

Endpoints (prefixed /api/v1/merchant/support):

| Method | Path         | Description                        |
|--------|--------------|------------------------------------|
| POST   | /tickets/    | Submit a new support ticket        |
| GET    | /tickets/    | List own tickets (paginated)       |
| GET    | /tickets/{id}| Get a single ticket detail         |
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.models.merchant_product import MerchantProduct
from app.models.support import (
    SupportMessage,
    SupportMessageAuthorType,
    SupportRequesterType,
    SupportTicket,
    SupportTicketPriority,
    SupportTicketStatus,
    new_support_reference,
)
from app.schemas.support import (
    PaginatedSupportTickets,
    SupportTicketCreate,
    SupportTicketOut,
)
from app.utils.dependencies import DBSession
from app.utils.merchant_context import CurrentMerchantContext, require_verified_merchant

router = APIRouter(
    prefix="/merchant/support",
    tags=["merchant-support"],
    dependencies=[Depends(require_verified_merchant)],
)

# SLA window: 48 h for new merchant tickets
_SLA_HOURS = 48


def _build_subject(reason: str, sub_reason: str) -> str:
    """Derive a human-readable subject from slugs by title-casing and replacing underscores."""
    def _fmt(slug: str) -> str:
        return slug.replace("_", " ").title()

    return f"{_fmt(reason)} — {_fmt(sub_reason)}"


async def _generate_unique_reference(db: DBSession) -> str:
    for _ in range(20):
        candidate = new_support_reference()
        existing = await db.scalar(
            select(SupportTicket.id).where(SupportTicket.reference == candidate)
        )
        if existing is None:
            return candidate
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Could not allocate a support ticket reference. Please retry.",
    )


@router.post("/tickets/", response_model=SupportTicketOut, status_code=status.HTTP_201_CREATED)
async def create_ticket(
    body: SupportTicketCreate,
    db: DBSession,
    ctx: CurrentMerchantContext,
) -> SupportTicket:
    """Submit a new support ticket on behalf of the authenticated merchant."""
    # Validate product belongs to this merchant if provided
    if body.merchant_product_id is not None:
        product = await db.get(MerchantProduct, body.merchant_product_id)
        if product is None or product.merchant_id != ctx.merchant.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Product not found or does not belong to this merchant.",
            )

    sla_due = datetime.now(timezone.utc) + timedelta(hours=_SLA_HOURS)

    ticket = SupportTicket(
        reference=await _generate_unique_reference(db),
        subject=_build_subject(body.reason, body.sub_reason),
        requester_type=SupportRequesterType.MERCHANT.value,
        requester_id=ctx.merchant.id,
        status=SupportTicketStatus.OPEN.value,
        priority=SupportTicketPriority.MEDIUM.value,
        sla_due_at=sla_due,
        reason=body.reason,
        sub_reason=body.sub_reason,
        merchant_product_id=body.merchant_product_id,
        attachment_url=body.attachment_url,
        description=body.description,
    )
    db.add(ticket)

    # Create the first message from the description body
    first_message = SupportMessage(
        ticket=ticket,
        author_type=SupportMessageAuthorType.REQUESTER.value,
        author_id=ctx.merchant.id,
        body=body.description,
    )
    db.add(first_message)

    await db.commit()
    await db.refresh(ticket)
    return ticket


@router.get("/tickets/", response_model=PaginatedSupportTickets)
async def list_tickets(
    db: DBSession,
    ctx: CurrentMerchantContext,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """Return all support tickets submitted by this merchant, newest first."""
    base = select(SupportTicket).where(
        SupportTicket.requester_type == SupportRequesterType.MERCHANT.value,
        SupportTicket.requester_id == ctx.merchant.id,
        SupportTicket.deleted_at.is_(None),
    )
    count_q = select(func.count()).select_from(SupportTicket).where(
        SupportTicket.requester_type == SupportRequesterType.MERCHANT.value,
        SupportTicket.requester_id == ctx.merchant.id,
        SupportTicket.deleted_at.is_(None),
    )

    total = (await db.execute(count_q)).scalar_one()
    rows = (
        await db.execute(
            base.order_by(SupportTicket.created_at.desc()).offset(offset).limit(limit)
        )
    ).scalars().all()

    return {"items": list(rows), "total": total, "limit": limit, "offset": offset}


@router.get("/tickets/{ticket_id}", response_model=SupportTicketOut)
async def get_ticket(
    ticket_id: str,
    db: DBSession,
    ctx: CurrentMerchantContext,
) -> SupportTicket:
    """Return a single ticket owned by the authenticated merchant."""
    try:
        parsed_id = uuid.UUID(ticket_id)
    except ValueError:
        parsed_id = None
    stmt = select(SupportTicket).where(
        SupportTicket.requester_type == SupportRequesterType.MERCHANT.value,
        SupportTicket.requester_id == ctx.merchant.id,
        SupportTicket.deleted_at.is_(None),
    )
    if parsed_id is not None:
        stmt = stmt.where(SupportTicket.id == parsed_id)
    else:
        stmt = stmt.where(SupportTicket.reference == ticket_id.upper())
    ticket = (await db.execute(stmt)).scalar_one_or_none()
    if (
        ticket is None
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found.")
    return ticket

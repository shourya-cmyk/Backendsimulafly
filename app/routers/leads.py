"""Merchant-facing leads + orders router.

GET  /merchant/leads/          -- paginated list (filter by status, lead_type)
GET  /merchant/leads/{id}      -- lead detail with order + customer info
PATCH /merchant/leads/{id}     -- update status + merchant_notes
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lead import BuyerLead, LeadStatus, Order, OrderStatus
from app.models.user import User
from app.schemas.lead import (
    BuyerLeadOut,
    BuyerLeadUpdate,
    CustomerInfo,
    OrderOut,
    PaginatedLeads,
)
from app.services.billing import BillingService
from app.utils.dependencies import DBSession
from app.utils.merchant_context import CurrentMerchantContext

router = APIRouter(prefix="/merchant/leads", tags=["merchant-leads"])


# ── helpers ───────────────────────────────────────────────────────────────────

async def _build_lead_out(
    lead: BuyerLead, db: AsyncSession, reveal_pii: bool
) -> BuyerLeadOut:
    """Assemble a BuyerLeadOut from the lead row, joining user + order."""
    user = await db.get(User, lead.user_id)

    if reveal_pii and user:
        customer = CustomerInfo(
            city=lead.delivery_city,
            name=user.full_name,
            email=user.email,
            phone=lead.delivery_phone,
        )
    else:
        customer = CustomerInfo(city=lead.delivery_city)

    res = await db.execute(select(Order).where(Order.lead_id == lead.id))
    order_row = res.scalar_one_or_none()
    order_out = OrderOut.model_validate(order_row) if order_row else None

    return BuyerLeadOut(
        id=lead.id,
        merchant_id=lead.merchant_id,
        lead_type=lead.lead_type,
        status=lead.status,
        estimated_value=lead.estimated_value,
        ai_interactions_count=lead.ai_interactions_count,
        ai_generated_image_url=lead.ai_generated_image_url,
        delivery_city=lead.delivery_city,
        merchant_notes=lead.merchant_notes,
        converted_at=lead.converted_at,
        created_at=lead.created_at,
        updated_at=lead.updated_at,
        customer=customer,
        order=order_out,
    )


# ── endpoints ─────────────────────────────────────────────────────────────────

@router.get("/", response_model=PaginatedLeads)
async def list_leads(
    ctx: CurrentMerchantContext,
    db: DBSession,
    lead_status: str | None = Query(default=None, alias="status"),
    lead_type: str | None = Query(default=None),
    limit: int = Query(default=25, le=100),
    offset: int = Query(default=0, ge=0),
):
    q = select(BuyerLead).where(BuyerLead.merchant_id == ctx.merchant.id)
    if lead_status:
        q = q.where(BuyerLead.status == lead_status)
    if lead_type:
        q = q.where(BuyerLead.lead_type == lead_type)

    total_res = await db.execute(select(func.count()).select_from(q.subquery()))
    total = total_res.scalar_one()

    q = q.order_by(BuyerLead.created_at.desc()).limit(limit).offset(offset)
    res = await db.execute(q)
    leads = res.scalars().all()

    items = [
        await _build_lead_out(lead, db, reveal_pii=(lead.status != LeadStatus.NEW.value))
        for lead in leads
    ]
    return PaginatedLeads(items=items, total=total, limit=limit, offset=offset)


@router.get("/{lead_id}", response_model=BuyerLeadOut)
async def get_lead(
    lead_id: uuid.UUID,
    ctx: CurrentMerchantContext,
    db: DBSession,
):
    lead = await db.get(BuyerLead, lead_id)
    if not lead or lead.merchant_id != ctx.merchant.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="lead not found")
    return await _build_lead_out(lead, db, reveal_pii=(lead.status != LeadStatus.NEW.value))


@router.patch("/{lead_id}", response_model=BuyerLeadOut)
async def update_lead(
    lead_id: uuid.UUID,
    body: BuyerLeadUpdate,
    ctx: CurrentMerchantContext,
    db: DBSession,
):
    lead = await db.get(BuyerLead, lead_id)
    if not lead or lead.merchant_id != ctx.merchant.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="lead not found")

    if body.status is not None:
        allowed = {s.value for s in LeadStatus}
        if body.status not in allowed:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"status must be one of {sorted(allowed)}",
            )
        lead.status = body.status

        if body.status == LeadStatus.CONVERTED.value:
            lead.converted_at = datetime.now(timezone.utc)
            # Complete the order + bill the simulafly_purchase fee
            res = await db.execute(select(Order).where(Order.lead_id == lead.id))
            order = res.scalar_one_or_none()
            if order and order.status != OrderStatus.COMPLETED.value:
                order.status = OrderStatus.COMPLETED.value
                order.completed_at = datetime.now(timezone.utc)
                svc = BillingService(db)
                await svc.transaction_fee_on_conversion(order=order)

        elif body.status == LeadStatus.SYNCED.value:
            res = await db.execute(select(Order).where(Order.lead_id == lead.id))
            order = res.scalar_one_or_none()
            if order and order.status == OrderStatus.PENDING_MERCHANT_CONTACT.value:
                order.status = OrderStatus.CONTACTED.value

        elif body.status == LeadStatus.LOST.value:
            res = await db.execute(select(Order).where(Order.lead_id == lead.id))
            order = res.scalar_one_or_none()
            if order and order.status not in (
                OrderStatus.COMPLETED.value, OrderStatus.CANCELLED.value
            ):
                order.status = OrderStatus.CANCELLED.value

    if body.merchant_notes is not None:
        lead.merchant_notes = body.merchant_notes

    await db.commit()
    await db.refresh(lead)
    return await _build_lead_out(lead, db, reveal_pii=(lead.status != LeadStatus.NEW.value))

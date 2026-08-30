"""Merchant-facing leads + orders router.

GET  /merchant/leads/          -- paginated list (filter by status, lead_type)
GET  /merchant/leads/{id}      -- lead detail with order + customer info
PATCH /merchant/leads/{id}     -- update status + merchant_notes
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
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
from app.utils.merchant_context import CurrentMerchantContext, require_verified_merchant

router = APIRouter(
    prefix="/merchant/leads",
    tags=["merchant-leads"],
    dependencies=[Depends(require_verified_merchant)],
)


# ── helpers ───────────────────────────────────────────────────────────────────

async def _build_lead_out(
    lead: BuyerLead, db: AsyncSession, reveal_pii: bool
) -> BuyerLeadOut:
    """Assemble a BuyerLeadOut from the lead row, joining user + order."""
    user = await db.get(User, lead.user_id)

    res = await db.execute(select(Order).where(Order.lead_id == lead.id))
    order_row = res.scalar_one_or_none()
    order_out = OrderOut.model_validate(order_row) if order_row else None

    addr_dict = order_row.delivery_address if (order_row and order_row.delivery_address) else {}

    if reveal_pii and user:
        customer = CustomerInfo(
            city=addr_dict.get("city") or lead.delivery_city,
            name=user.full_name,
            email=user.email,
            phone=addr_dict.get("phone") or lead.delivery_phone,
            address_line1=addr_dict.get("address_line1") or user.address_line1,
            state=addr_dict.get("state") or user.state,
            pincode=addr_dict.get("pincode") or user.pincode,
            latitude=addr_dict.get("latitude"),
            longitude=addr_dict.get("longitude"),
        )
    else:
        customer = CustomerInfo(
            city=addr_dict.get("city") or lead.delivery_city,
        )

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
        cancellation_reason=lead.cancellation_reason,
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
            
            # Credit user ₹20 on conversion
            user_obj = await db.get(User, lead.user_id)
            if user_obj:
                user_obj.credit_balance = (user_obj.credit_balance or 0.0) + 20.0
            
            # Create order completed notification
            from app.models.notification import Notification
            notif = Notification(
                user_id=lead.user_id,
                kind="delivery",
                title="Order Confirmed",
                summary=f"Your order with {ctx.merchant.display_name} has been confirmed by the merchant!",
                payload={"lead_id": str(lead.id), "status": "converted"}
            )
            db.add(notif)

        elif body.status == LeadStatus.SYNCED.value:
            res = await db.execute(select(Order).where(Order.lead_id == lead.id))
            order = res.scalar_one_or_none()
            if order and order.status == OrderStatus.PENDING_MERCHANT_CONTACT.value:
                order.status = OrderStatus.CONTACTED.value
                
                # Create order contacted notification
                from app.models.notification import Notification
                notif = Notification(
                    user_id=lead.user_id,
                    kind="delivery",
                    title="Merchant Contacted You",
                    summary=f"The merchant {ctx.merchant.display_name} has updated your order status to Contacted.",
                    payload={"lead_id": str(lead.id), "status": "contacted"}
                )
                db.add(notif)

        elif body.status == LeadStatus.LOST.value:
            # Persist cancellation reason if provided
            if body.cancellation_reason is not None:
                lead.cancellation_reason = body.cancellation_reason.model_dump()

            res = await db.execute(select(Order).where(Order.lead_id == lead.id))
            order = res.scalar_one_or_none()
            if order and order.status not in (
                OrderStatus.COMPLETED.value, OrderStatus.CANCELLED.value
            ):
                order.status = OrderStatus.CANCELLED.value

                # Build a human-readable reason string for the notification
                reason_parts = []
                if body.cancellation_reason:
                    reason_parts.append(
                        f"{body.cancellation_reason.parent_reason} › "
                        f"{body.cancellation_reason.child_reason}"
                    )
                    if body.cancellation_reason.note:
                        reason_parts.append(body.cancellation_reason.note)
                reason_text = ": ".join(reason_parts) if reason_parts else "No reason provided"

                # Create order cancelled notification
                from app.models.notification import Notification
                notif = Notification(
                    user_id=lead.user_id,
                    kind="delivery",
                    title="Order Cancelled",
                    summary=(
                        f"Your order with {ctx.merchant.display_name} was cancelled. "
                        f"Reason: {reason_text}"
                    ),
                    payload={
                        "lead_id": str(lead.id),
                        "status": "lost",
                        "cancellation_reason": lead.cancellation_reason,
                    }
                )
                db.add(notif)

    if body.merchant_notes is not None:
        lead.merchant_notes = body.merchant_notes

    await db.commit()
    await db.refresh(lead)
    return await _build_lead_out(lead, db, reveal_pii=(lead.status != LeadStatus.NEW.value))

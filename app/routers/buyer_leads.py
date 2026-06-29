"""Buyer-facing lead submission router (Flutter → 'Buy on SimulaFly').

POST /buyer/leads/     -- submit a purchase-intent lead
GET  /buyer/leads/me   -- buyer's own lead history
"""
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from app.models.event import BuyerEvent
from app.models.lead import BuyerLead, LeadStatus, LeadType, Order, OrderStatus
from app.models.merchant_product import MerchantProduct
from app.schemas.lead import (
    BuyerLeadCreate,
    BuyerLeadOut,
    CustomerInfo,
    OrderOut,
    PaginatedLeads,
)
from app.utils.dependencies import CurrentUser, DBSession

router = APIRouter(prefix="/buyer/leads", tags=["buyer-leads"])


@router.post("/", response_model=BuyerLeadOut, status_code=status.HTTP_201_CREATED)
async def submit_lead(
    body: BuyerLeadCreate,
    user: CurrentUser,
    db: DBSession,
):
    stmt = (
        select(MerchantProduct)
        .options(selectinload(MerchantProduct.merchant))
        .where(MerchantProduct.id == body.merchant_product_id)
    )
    product = (await db.execute(stmt)).scalar_one_or_none()
    if not product or product.status != "published":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="product not found or unavailable",
        )

    # Check range restriction
    lat = body.delivery_latitude if body.delivery_latitude is not None else user.latitude
    lon = body.delivery_longitude if body.delivery_longitude is not None else user.longitude
    if lat is not None and lon is not None and product.merchant:
        m = product.merchant
        if m.latitude is not None and m.longitude is not None and m.range_km is not None:
            import math
            def calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
                R = 6371.0  # Earth's radius in km
                dlat = math.radians(lat2 - lat1)
                dlon = math.radians(lon2 - lon1)
                a = (
                    math.sin(dlat / 2) ** 2
                    + math.cos(math.radians(lat1))
                    * math.cos(math.radians(lat2))
                    * math.sin(dlon / 2) ** 2
                )
                c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
                return R * c

            dist = calculate_distance(lat, lon, m.latitude, m.longitude)
            if dist > m.range_km:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="This shop does not serve your location."
                )

    # Count AI interactions for this buyer+product (best-effort)
    count_res = await db.execute(
        select(func.count()).where(
            BuyerEvent.user_id == user.id,
            BuyerEvent.merchant_product_id == product.id,
        )
    )
    ai_count: int = count_res.scalar_one()

    # Build items list
    if body.items:
        items_payload = [
            {
                "product_id": str(i.product_id),
                "variant_id": str(i.variant_id) if i.variant_id else None,
                "qty": i.qty,
                "price_at_capture": float(i.price_at_capture),
                "title": i.title,
                "img_url": i.img_url,
                "sku": i.sku,
            }
            for i in body.items
        ]
        total = sum(
            Decimal(str(i.price_at_capture)) * i.qty for i in body.items
        )
    else:
        # Fallback: single unit of the main product
        items_payload = [
            {
                "product_id": str(product.id),
                "variant_id": None,
                "qty": 1,
                "price_at_capture": float(product.in_app_price or 0),
                "title": product.title,
                "img_url": product.primary_image_url,
                "sku": product.sku,
            }
        ]
        total = Decimal(str(product.in_app_price or 0))

    lead = BuyerLead(
        merchant_id=product.merchant_id,
        user_id=user.id,
        lead_type=LeadType.DIRECT_PURCHASE.value,
        status=LeadStatus.NEW.value,
        product_ids=[str(product.id)],
        ai_interactions_count=ai_count,
        estimated_value=total,
        # Fall back to user's saved profile if not provided at checkout time
        delivery_city=body.delivery_city or user.city,
        delivery_phone=body.delivery_phone or user.phone,
    )
    db.add(lead)
    await db.flush()

    order = Order(
        lead_id=lead.id,
        merchant_id=product.merchant_id,
        user_id=user.id,
        status=OrderStatus.PENDING_MERCHANT_CONTACT.value,
        items=items_payload,
        total_estimated=total,
        delivery_address={
            "city": lead.delivery_city,
            "phone": lead.delivery_phone,
            "address_line1": body.delivery_address_line1 or user.address_line1,
            "state": body.delivery_state or user.state,
            "pincode": body.delivery_pincode or user.pincode,
            "latitude": body.delivery_latitude,
            "longitude": body.delivery_longitude,
        },
    )
    db.add(order)
    await db.commit()
    await db.refresh(lead)
    await db.refresh(order)

    # Notify merchant members about the new lead
    try:
        from app.models.merchant import MerchantMember
        from app.models.notification import Notification

        members_res = await db.execute(
            select(MerchantMember).where(MerchantMember.merchant_id == product.merchant_id)
        )
        merchant_members = members_res.scalars().all()

        for member in merchant_members:
            notif = Notification(
                user_id=member.user_id,
                kind="system",
                title="New Lead Received",
                summary=f"A new direct purchase lead has been received for product: {product.title}",
                payload={"lead_id": str(lead.id), "merchant_id": str(product.merchant_id)}
            )
            db.add(notif)
        await db.commit()
    except Exception as e:
        import logging
        logger = logging.getLogger("app.routers.buyer_leads")
        logger.warning(f"Failed to create merchant notifications: {e}")

    return BuyerLeadOut(
        id=lead.id,
        merchant_id=lead.merchant_id,
        lead_type=lead.lead_type,
        status=lead.status,
        estimated_value=lead.estimated_value,
        ai_interactions_count=lead.ai_interactions_count,
        ai_generated_image_url=None,
        delivery_city=lead.delivery_city,
        merchant_notes=None,
        converted_at=None,
        created_at=lead.created_at,
        updated_at=lead.updated_at,
        customer=CustomerInfo(
            city=lead.delivery_city,
            name=user.full_name,
            email=user.email,
            phone=lead.delivery_phone,
            address_line1=body.delivery_address_line1 or user.address_line1,
            state=body.delivery_state or user.state,
            pincode=body.delivery_pincode or user.pincode,
            latitude=body.delivery_latitude,
            longitude=body.delivery_longitude,
        ),
        order=OrderOut(
            id=order.id,
            status=order.status,
            items=order.items,
            total_estimated=order.total_estimated,
            completed_at=None,
            created_at=order.created_at,
            updated_at=order.updated_at,
        ),
    )


@router.get("/me", response_model=PaginatedLeads)
async def my_leads(
    user: CurrentUser,
    db: DBSession,
    limit: int = Query(default=25, le=100),
    offset: int = Query(default=0, ge=0),
):
    q = select(BuyerLead).where(BuyerLead.user_id == user.id)

    total_res = await db.execute(select(func.count()).select_from(q.subquery()))
    total = total_res.scalar_one()

    q = q.order_by(BuyerLead.created_at.desc()).limit(limit).offset(offset)
    res = await db.execute(q)
    leads = res.scalars().all()

    items = []
    for lead in leads:
        order_res = await db.execute(select(Order).where(Order.lead_id == lead.id))
        order_row = order_res.scalar_one_or_none()
        
        addr_dict = order_row.delivery_address if (order_row and order_row.delivery_address) else {}
        
        items.append(
            BuyerLeadOut(
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
                customer=CustomerInfo(
                    city=addr_dict.get("city") or lead.delivery_city,
                    name=user.full_name,
                    email=user.email,
                    phone=addr_dict.get("phone") or lead.delivery_phone,
                    address_line1=addr_dict.get("address_line1") or user.address_line1,
                    state=addr_dict.get("state") or user.state,
                    pincode=addr_dict.get("pincode") or user.pincode,
                    latitude=addr_dict.get("latitude"),
                    longitude=addr_dict.get("longitude"),
                ),
                order=OrderOut.model_validate(order_row) if order_row else None,
            )
        )

    return PaginatedLeads(items=items, total=total, limit=limit, offset=offset)

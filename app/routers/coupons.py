import uuid
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.coupon import MerchantCoupon, DiscountType
from app.models.merchant import Merchant
from app.schemas.coupon import (
    CouponCreate,
    CouponOut,
    CouponValidateRequest,
    CouponValidateResponse,
)

router = APIRouter(prefix="/coupons", tags=["coupons"])

DBSession = Annotated[AsyncSession, Depends(get_db)]

SEED_COUPONS = [
    {
        "code": "FESTIVE200",
        "title": "Festive Special ₹200 OFF",
        "discount_type": "flat",
        "discount_value": Decimal("200.0"),
        "min_order_amount": Decimal("500.0"),
    },
    {
        "code": "SUPER500",
        "title": "Super Saver ₹500 OFF",
        "discount_type": "flat",
        "discount_value": Decimal("500.0"),
        "min_order_amount": Decimal("1500.0"),
    },
    {
        "code": "SIMULA1000",
        "title": "Simulafly Premium ₹1000 OFF",
        "discount_type": "flat",
        "discount_value": Decimal("1000.0"),
        "min_order_amount": Decimal("3000.0"),
    },
    {
        "code": "SAVE10",
        "title": "10% Merchant Discount",
        "discount_type": "percentage",
        "discount_value": Decimal("10.0"),
        "min_order_amount": Decimal("0.0"),
        "max_discount_amount": Decimal("2000.0"),
    },
]


async def _seed_coupons_if_empty(db: AsyncSession):
    res = await db.execute(select(MerchantCoupon))
    if not res.scalars().first():
        # Get first merchant id if available
        m_res = await db.execute(select(Merchant).limit(1))
        merchant = m_res.scalars().first()
        m_id = merchant.id if merchant else None

        for seed in SEED_COUPONS:
            coupon = MerchantCoupon(
                merchant_id=m_id,
                code=seed["code"],
                title=seed["title"],
                discount_type=seed["discount_type"],
                discount_value=seed["discount_value"],
                min_order_amount=seed["min_order_amount"],
                max_discount_amount=seed.get("max_discount_amount"),
                is_active=True,
            )
            db.add(coupon)
        await db.commit()


@router.get("/public", response_model=list[CouponOut])
async def get_public_coupons(
    db: DBSession,
    merchant_id: uuid.UUID | None = Query(default=None),
) -> list:
    """Fetch active merchant created coupons from backend DB."""
    await _seed_coupons_if_empty(db)

    stmt = select(MerchantCoupon).where(MerchantCoupon.is_active == True)  # noqa: E712
    if merchant_id:
        stmt = stmt.where(
            (MerchantCoupon.merchant_id == merchant_id) | (MerchantCoupon.merchant_id.is_(None))
        )

    res = await db.execute(stmt)
    return res.scalars().all()


@router.post("/validate", response_model=CouponValidateResponse)
async def validate_coupon(
    body: CouponValidateRequest,
    db: DBSession,
) -> dict:
    """Validate a merchant coupon code against order amount."""
    await _seed_coupons_if_empty(db)

    clean_code = body.code.strip().upper()
    if not clean_code:
        return {"valid": False, "code": body.code, "reason": "Empty coupon code"}

    stmt = select(MerchantCoupon).where(
        MerchantCoupon.code == clean_code,
        MerchantCoupon.is_active == True,  # noqa: E712
    )
    res = await db.execute(stmt)
    coupon = res.scalars().first()

    if not coupon:
        return {
            "valid": False,
            "code": clean_code,
            "reason": f"Invalid or expired coupon code '{clean_code}'",
        }

    order_amt = Decimal(str(body.order_amount))
    if coupon.min_order_amount and order_amt < coupon.min_order_amount:
        return {
            "valid": False,
            "code": clean_code,
            "reason": f"Minimum order amount for {clean_code} is ₹{float(coupon.min_order_amount):.0f}",
        }

    # Calculate discount
    if coupon.discount_type == DiscountType.PERCENTAGE.value:
        calculated = (order_amt * coupon.discount_value / Decimal("100.0")).quantize(Decimal("0.01"))
        if coupon.max_discount_amount and calculated > coupon.max_discount_amount:
            calculated = coupon.max_discount_amount
    else:
        calculated = coupon.discount_value

    final_discount = min(calculated, order_amt) if order_amt > 0 else calculated

    return {
        "valid": True,
        "code": coupon.code,
        "discount_type": coupon.discount_type,
        "discount_value": float(coupon.discount_value),
        "discount_amount": float(final_discount),
        "title": coupon.title,
    }


@router.post("/merchant/create", response_model=CouponOut, status_code=status.HTTP_201_CREATED)
async def create_merchant_coupon(
    body: CouponCreate,
    db: DBSession,
) -> MerchantCoupon:
    """Endpoint for merchants to create custom coupon codes dynamically."""
    code_clean = body.code.strip().upper()

    # Check duplicate
    stmt = select(MerchantCoupon).where(MerchantCoupon.code == code_clean)
    res = await db.execute(stmt)
    if res.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Coupon code '{code_clean}' already exists.",
        )

    coupon = MerchantCoupon(
        merchant_id=body.merchant_id,
        code=code_clean,
        title=body.title,
        discount_type=body.discount_type,
        discount_value=body.discount_value,
        min_order_amount=body.min_order_amount,
        max_discount_amount=body.max_discount_amount,
        usage_limit=body.usage_limit,
        is_active=True,
    )
    db.add(coupon)
    await db.commit()
    await db.refresh(coupon)
    return coupon

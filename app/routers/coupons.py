import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import or_, select

from app.models.coupon import MerchantCoupon
from app.schemas.coupon import (
    CouponCreate,
    CouponOut,
    CouponValidateRequest,
    CouponValidateResponse,
)
from app.services.coupons import check_coupon
from app.utils.dependencies import DBSession
from app.utils.merchant_context import VerifiedMerchantContext

router = APIRouter(prefix="/coupons", tags=["coupons"])


@router.get("/public", response_model=list[CouponOut])
async def get_public_coupons(
    db: DBSession,
    merchant_id: uuid.UUID | None = Query(default=None),
) -> list[MerchantCoupon]:
    """Return only active coupons created by the requested merchant."""
    if merchant_id is None:
        return []

    stmt = (
        select(MerchantCoupon)
        .where(
            MerchantCoupon.merchant_id == merchant_id,
            MerchantCoupon.is_active.is_(True),
            or_(
                MerchantCoupon.expires_at.is_(None),
                MerchantCoupon.expires_at > datetime.now(timezone.utc),
            ),
            or_(
                MerchantCoupon.usage_limit.is_(None),
                MerchantCoupon.used_count < MerchantCoupon.usage_limit,
            ),
        )
        .order_by(MerchantCoupon.created_at.desc())
    )
    return list((await db.execute(stmt)).scalars().all())


@router.post("/validate", response_model=CouponValidateResponse)
async def validate_coupon(
    body: CouponValidateRequest,
    db: DBSession,
) -> dict:
    """Validate a coupon against its owning merchant and the order amount."""
    result = await check_coupon(
        db,
        code=body.code,
        merchant_id=body.merchant_id,
        order_amount=body.order_amount,
    )
    if not result.valid:
        return {
            "valid": False,
            "code": result.code,
            "reason": result.reason,
        }

    coupon = result.coupon
    assert coupon is not None
    return {
        "valid": True,
        "code": coupon.code,
        "discount_type": coupon.discount_type,
        "discount_value": float(coupon.discount_value),
        "discount_amount": float(result.discount_amount),
        "title": coupon.title,
    }


@router.get("/merchant", response_model=list[CouponOut])
async def list_merchant_coupons(
    ctx: VerifiedMerchantContext,
    db: DBSession,
) -> list[MerchantCoupon]:
    """List coupons belonging to the authenticated merchant."""
    stmt = (
        select(MerchantCoupon)
        .where(MerchantCoupon.merchant_id == ctx.merchant.id)
        .order_by(MerchantCoupon.created_at.desc())
    )
    return list((await db.execute(stmt)).scalars().all())


@router.post(
    "/merchant/create",
    response_model=CouponOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_merchant_coupon(
    body: CouponCreate,
    ctx: VerifiedMerchantContext,
    db: DBSession,
) -> MerchantCoupon:
    """Create a coupon owned by the authenticated merchant."""
    code_clean = body.code.strip().upper()

    stmt = select(MerchantCoupon).where(
        MerchantCoupon.merchant_id == ctx.merchant.id,
        MerchantCoupon.code == code_clean,
    )
    if (await db.execute(stmt)).scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Coupon code '{code_clean}' already exists for this merchant.",
        )

    coupon = MerchantCoupon(
        merchant_id=ctx.merchant.id,
        code=code_clean,
        title=body.title.strip(),
        discount_type=body.discount_type,
        discount_value=body.discount_value,
        min_order_amount=body.min_order_amount,
        max_discount_amount=body.max_discount_amount,
        usage_limit=body.usage_limit,
        expires_at=body.expires_at,
        is_active=True,
    )
    db.add(coupon)
    await db.commit()
    await db.refresh(coupon)
    return coupon

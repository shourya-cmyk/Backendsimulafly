"""Merchant coupon validation and discount calculation."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.coupon import DiscountType, MerchantCoupon


@dataclass(frozen=True)
class CouponCheck:
    coupon: MerchantCoupon | None
    code: str
    discount_amount: Decimal = Decimal("0")
    reason: str | None = None

    @property
    def valid(self) -> bool:
        return self.coupon is not None


async def check_coupon(
    db: AsyncSession,
    *,
    code: str,
    merchant_id: uuid.UUID,
    order_amount: Decimal,
    lock: bool = False,
) -> CouponCheck:
    """Validate a coupon owned by ``merchant_id`` and calculate its discount."""
    clean_code = code.strip().upper()
    if not clean_code:
        return CouponCheck(coupon=None, code=clean_code, reason="Empty coupon code")

    stmt = select(MerchantCoupon).where(
        MerchantCoupon.merchant_id == merchant_id,
        MerchantCoupon.code == clean_code,
        MerchantCoupon.is_active.is_(True),
    )
    if lock:
        stmt = stmt.with_for_update()

    coupon = (await db.execute(stmt)).scalar_one_or_none()
    if coupon is None:
        return CouponCheck(
            coupon=None,
            code=clean_code,
            reason=f"Invalid or expired coupon code '{clean_code}'",
        )

    now = datetime.now(timezone.utc)
    expires_at = coupon.expires_at
    if expires_at is not None:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= now:
            return CouponCheck(
                coupon=None,
                code=clean_code,
                reason=f"Coupon code '{clean_code}' has expired",
            )

    if coupon.usage_limit is not None and coupon.used_count >= coupon.usage_limit:
        return CouponCheck(
            coupon=None,
            code=clean_code,
            reason=f"Coupon code '{clean_code}' has reached its usage limit",
        )

    if order_amount < coupon.min_order_amount:
        return CouponCheck(
            coupon=None,
            code=clean_code,
            reason=(
                f"Minimum order amount for {clean_code} is "
                f"₹{float(coupon.min_order_amount):.0f}"
            ),
        )

    if coupon.discount_type == DiscountType.PERCENTAGE.value:
        calculated = (
            order_amount * coupon.discount_value / Decimal("100")
        ).quantize(Decimal("0.01"))
        if (
            coupon.max_discount_amount is not None
            and calculated > coupon.max_discount_amount
        ):
            calculated = coupon.max_discount_amount
    else:
        calculated = coupon.discount_value

    discount = min(calculated, order_amount) if order_amount > 0 else Decimal("0")
    return CouponCheck(
        coupon=coupon,
        code=coupon.code,
        discount_amount=discount,
    )

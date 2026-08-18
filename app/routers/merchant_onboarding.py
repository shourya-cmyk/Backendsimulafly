from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.config import get_settings
from app.models.user import User
from app.schemas.merchant import MerchantOut
from app.schemas.merchant_onboarding import MerchantOnboardingSubmission
from app.utils.dependencies import DBSession
from app.utils.merchant_context import MerchantContext, require_role


router = APIRouter(
    prefix="/merchants/{merchant_id}/onboarding",
    tags=["merchant-onboarding"],
)


def _normalize_phone(value: str) -> str:
    return value.strip().replace(" ", "").replace("-", "")


def _pan_fingerprint(pan: str) -> str:
    return hmac.new(
        get_settings().SECRET_KEY.encode("utf-8"),
        pan.strip().upper().encode("ascii"),
        hashlib.sha256,
    ).hexdigest()


@router.post("/submit", response_model=MerchantOut)
async def submit_onboarding(
    merchant_id: uuid.UUID,
    body: MerchantOnboardingSubmission,
    db: DBSession,
    ctx: MerchantContext = Depends(require_role("owner")),
):
    if ctx.merchant.id != merchant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Merchant-Id header must match path merchant_id",
        )
    user = await db.get(User, ctx.member.user_id)
    if user is None:
        raise HTTPException(status_code=409, detail="Merchant owner account was not found")
    if body.personal.email.lower() != user.email.lower():
        raise HTTPException(status_code=400, detail="Personal email must match the signed-in account")
    if not user.is_email_verified:
        raise HTTPException(status_code=409, detail="Verify your email before submitting onboarding")
    if not user.phone or _normalize_phone(user.phone) != _normalize_phone(body.personal.phone):
        raise HTTPException(status_code=409, detail="Verify this phone number by OTP before submitting")
    if body.shop.contact_number != body.personal.phone:
        raise HTTPException(status_code=400, detail="Shop contact must match the verified phone number")

    now = datetime.now(timezone.utc).isoformat()
    raw = body.model_dump(mode="json")
    pan = raw["business"].pop("business_pan")
    raw["business"]["business_pan_masked"] = f"******{pan[-4:]}"
    raw["business"]["business_pan_fingerprint"] = _pan_fingerprint(pan)
    raw["submitted_at"] = now

    checks = {
        "authorized_person": True,
        "business_address": True,
        "shop_location": True,
    }
    existing = dict(ctx.merchant.settings or {})
    existing.update(
        {
            "onboarding_submission": raw,
            "onboarding_checks": checks,
            "onboarding_completed": True,
            "approval_status": "pending_verification",
            "approved_at": None,
            # Retain the established storefront keys consumed elsewhere in the app.
            "onboarding_data": {
                "gst_number": body.shop.gstin,
                "company_type": body.business.business_type,
                "city": body.shop.shop_address.city,
                "locality": body.shop.shop_address.line1,
                "state": body.shop.shop_address.state,
            },
        }
    )

    user.full_name = body.personal.full_name
    ctx.merchant.legal_name = body.business.registered_business_name
    ctx.merchant.display_name = body.shop.shop_name
    ctx.merchant.support_email = body.personal.email.lower()
    ctx.merchant.support_phone = body.personal.phone
    ctx.merchant.range_km = body.shop.service_radius_km
    ctx.merchant.settings = existing
    await db.commit()
    await db.refresh(ctx.merchant)
    return ctx.merchant

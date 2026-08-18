import hashlib
import hmac
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.rate_limit import limiter
from app.models.merchant import Merchant, MerchantMember, MemberRole
from app.models.merchant_verification import GstinVerification, PanVerification
from app.schemas.merchant_verification import (
    AgreementAcceptanceRequest,
    AgreementStatusOut,
    GstinVerificationRequest,
    GstinVerificationStatusOut,
    MerchantVerificationOut,
    OtherVerificationChecksOut,
    PanVerificationRequest,
    PanVerificationStatusOut,
    VerificationCheckOut,
)
from app.services.sandbox_client import (
    SandboxAPIError,
    SandboxConfigurationError,
    get_sandbox_client,
)
from app.utils.dependencies import DBSession
from app.utils.merchant_context import MerchantContext, require_role


router = APIRouter(
    prefix="/merchants/{merchant_id}/verification",
    tags=["merchant-verification"],
)

AGREEMENT_VERSIONS = {
    "merchant_agreement": "2026-08-18",
    "terms_and_conditions": "2026-08-18",
    "privacy_policy": "2026-08-18",
    "marketplace_rules": "2026-08-18",
    "product_listing_policy": "2026-08-18",
    "cancellation_return_rules": "2026-08-18",
    "merchant_obligations_and_fees": "2026-08-18",
}


def _ensure_matching_merchant(merchant_id: uuid.UUID, ctx: MerchantContext) -> None:
    if ctx.merchant.id != merchant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Merchant-Id header must match path merchant_id",
        )


def _pan_fingerprint(pan: str) -> str:
    normalized = pan.strip().upper().encode("ascii")
    return hmac.new(
        get_settings().SECRET_KEY.encode("utf-8"), normalized, hashlib.sha256
    ).hexdigest()


async def _owner_user_id(db, merchant_id: uuid.UUID) -> uuid.UUID:
    result = await db.execute(
        select(MerchantMember.user_id).where(
            MerchantMember.merchant_id == merchant_id,
            MerchantMember.role == MemberRole.OWNER.value,
        )
    )
    owner_id = result.scalar_one_or_none()
    if owner_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This shop has no owner account to verify",
        )
    return owner_id


async def _load_records(db, merchant_id: uuid.UUID, owner_id: uuid.UUID):
    pan_result = await db.execute(
        select(PanVerification).where(PanVerification.user_id == owner_id)
    )
    gstin_result = await db.execute(
        select(GstinVerification).where(GstinVerification.merchant_id == merchant_id)
    )
    return pan_result.scalar_one_or_none(), gstin_result.scalar_one_or_none()


def _status_payload(
    *,
    merchant: Merchant,
    pan: PanVerification | None,
    gstin: GstinVerification | None,
    is_kyc_completed: bool,
) -> MerchantVerificationOut:
    settings = merchant.settings if isinstance(merchant.settings, dict) else {}
    raw_checks = settings.get("onboarding_checks")
    checks = raw_checks if isinstance(raw_checks, dict) else {}
    other_checks = OtherVerificationChecksOut(
        **{
            key: VerificationCheckOut(status="verified" if checks.get(key) is True else "pending")
            for key in (
                "authorized_person",
                "business_address",
                "shop_location",
            )
        }
    )
    raw_agreement = settings.get("agreement_acceptance")
    agreement_data = raw_agreement if isinstance(raw_agreement, dict) else {}
    all_other_verified = all(value is True for value in checks.values()) and len(checks) == 3
    approval_status = settings.get("approval_status", "draft")
    if approval_status not in {"draft", "pending_verification", "approved", "rejected"}:
        approval_status = "draft"
    return MerchantVerificationOut(
        pan=PanVerificationStatusOut(
            status="verified" if pan else "not_started",
            masked_pan=f"******{pan.pan_last_four}" if pan else None,
            verified_name=pan.verified_name if pan else None,
            category=pan.category if pan else None,
            verified_at=pan.verified_at if pan else None,
        ),
        gstin=GstinVerificationStatusOut(
            status="verified" if gstin else "not_started",
            gstin=gstin.gstin if gstin else None,
            legal_name=gstin.legal_name if gstin else None,
            business_nature=gstin.business_nature if gstin else None,
            state_name=gstin.state_name if gstin else None,
            registration_status=gstin.registration_status if gstin else None,
            verified_at=gstin.verified_at if gstin else None,
        ),
        other_checks=other_checks,
        agreement=AgreementStatusOut(
            accepted=agreement_data.get("accepted") is True,
            accepted_at=agreement_data.get("accepted_at"),
            versions=agreement_data.get("versions")
            if isinstance(agreement_data.get("versions"), dict)
            else {},
        ),
        is_kyc_completed=is_kyc_completed,
        approval_status=approval_status,
        can_activate=(
            is_kyc_completed
            and all_other_verified
            and approval_status != "approved"
        ),
    )


def _provider_error(exc: SandboxAPIError, document: str) -> HTTPException:
    if exc.status_code == 422:
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{document} format was rejected by the verification provider: {exc.message}",
        )
    if exc.status_code in (401, 403):
        detail = f"Verification provider credentials were rejected: {exc.message}"
    elif exc.status_code == 404:
        detail = exc.message or f"{document} verification record not found in provider database"
    elif exc.status_code >= 500:
        detail = "Verification provider is temporarily unavailable"
    else:
        detail = exc.message or f"{document} verification could not be completed"
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=detail)


async def _complete_kyc_if_ready(
    db,
    ctx: MerchantContext,
    pan: PanVerification | None,
    gstin: GstinVerification | None,
) -> bool:
    was_completed = ctx.merchant.is_kyc_completed
    ctx.merchant.is_kyc_completed = pan is not None and gstin is not None
    await db.commit()
    if ctx.merchant.is_kyc_completed and not was_completed:
        # Import lazily to keep router imports acyclic.
        from app.routers.merchants import process_kyc_welcome_bonus, process_referral_payout

        await process_referral_payout(db, ctx.merchant)
        await process_kyc_welcome_bonus(db, ctx.merchant)
    return ctx.merchant.is_kyc_completed


@router.get("", response_model=MerchantVerificationOut)
async def get_verification_status(
    merchant_id: uuid.UUID,
    db: DBSession,
    ctx: MerchantContext = Depends(require_role("owner", "admin")),
) -> MerchantVerificationOut:
    _ensure_matching_merchant(merchant_id, ctx)
    owner_id = await _owner_user_id(db, merchant_id)
    pan, gstin = await _load_records(db, merchant_id, owner_id)
    return _status_payload(
        merchant=ctx.merchant,
        pan=pan,
        gstin=gstin,
        is_kyc_completed=ctx.merchant.is_kyc_completed and pan is not None and gstin is not None,
    )


@router.post("/pan", response_model=MerchantVerificationOut)
@limiter.limit(f"{get_settings().KYC_RATE_LIMIT_PER_HOUR}/hour")
async def verify_pan(
    request: Request,
    response: Response,
    merchant_id: uuid.UUID,
    body: PanVerificationRequest,
    db: DBSession,
    ctx: MerchantContext = Depends(require_role("owner")),
) -> MerchantVerificationOut:
    _ensure_matching_merchant(merchant_id, ctx)
    owner_id = ctx.member.user_id
    pan, gstin = await _load_records(db, merchant_id, owner_id)
    fingerprint = _pan_fingerprint(body.pan)

    if pan is not None:
        if hmac.compare_digest(pan.pan_fingerprint, fingerprint):
            return _status_payload(
                merchant=ctx.merchant,
                pan=pan,
                gstin=gstin,
                is_kyc_completed=ctx.merchant.is_kyc_completed and gstin is not None,
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A different PAN is already verified for this account; contact support to change it",
        )

    onboarding = ctx.merchant.settings.get("onboarding_submission", {})
    expected_pan_fingerprint = (
        onboarding.get("business", {}).get("business_pan_fingerprint")
        if isinstance(onboarding, dict)
        else None
    )
    if expected_pan_fingerprint and not hmac.compare_digest(expected_pan_fingerprint, fingerprint):
        raise HTTPException(status_code=400, detail="PAN must match the PAN entered during business registration")

    try:
        result = await get_sandbox_client().verify_pan(
            pan=body.pan,
            name_as_per_pan=body.name_as_per_pan,
            date_of_birth=body.date_of_birth.strftime("%d/%m/%Y"),
        )
    except SandboxConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PAN verification is not configured",
        ) from exc
    except SandboxAPIError as exc:
        raise _provider_error(exc, "PAN") from exc

    if result.status.casefold() != "valid":
        raise HTTPException(status_code=400, detail="PAN is not valid")
    if not result.name_match:
        raise HTTPException(status_code=400, detail="Name does not match PAN records")
    if not result.date_of_birth_match:
        raise HTTPException(
            status_code=400,
            detail="Date of birth or incorporation does not match PAN records",
        )
    if result.remarks:
        raise HTTPException(
            status_code=400,
            detail=f"PAN cannot be approved: {result.remarks}",
        )

    pan = PanVerification(
        user_id=owner_id,
        pan_fingerprint=fingerprint,
        pan_last_four=body.pan[-4:],
        verified_name=body.name_as_per_pan,
        category=result.category,
        provider_transaction_id=result.transaction_id,
    )
    db.add(pan)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This PAN is already linked to another merchant account",
        ) from exc

    completed = await _complete_kyc_if_ready(db, ctx, pan, gstin)
    await db.refresh(pan)
    return _status_payload(
        merchant=ctx.merchant, pan=pan, gstin=gstin, is_kyc_completed=completed
    )


@router.post("/gstin", response_model=MerchantVerificationOut)
@limiter.limit(f"{get_settings().KYC_RATE_LIMIT_PER_HOUR}/hour")
async def verify_gstin(
    request: Request,
    response: Response,
    merchant_id: uuid.UUID,
    body: GstinVerificationRequest,
    db: DBSession,
    ctx: MerchantContext = Depends(require_role("owner")),
) -> MerchantVerificationOut:
    _ensure_matching_merchant(merchant_id, ctx)
    owner_id = ctx.member.user_id
    pan, gstin = await _load_records(db, merchant_id, owner_id)

    if pan is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Verify the merchant PAN before verifying this shop GSTIN",
        )
    if gstin is not None:
        if gstin.gstin == body.gstin:
            return _status_payload(
                merchant=ctx.merchant,
                pan=pan,
                gstin=gstin,
                is_kyc_completed=ctx.merchant.is_kyc_completed,
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A different GSTIN is already verified for this shop; contact support to change it",
        )

    onboarding = ctx.merchant.settings.get("onboarding_submission", {})
    expected_gstin = (
        onboarding.get("shop", {}).get("gstin") if isinstance(onboarding, dict) else None
    )
    if expected_gstin and expected_gstin != body.gstin:
        raise HTTPException(status_code=400, detail="GSTIN must match the GSTIN entered for this shop")

    try:
        result = await get_sandbox_client().verify_gstin(gstin=body.gstin)
    except SandboxConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GSTIN verification is not configured",
        ) from exc
    except SandboxAPIError as exc:
        raise _provider_error(exc, "GSTIN") from exc

    if not result.valid_gstin or result.gstin != body.gstin:
        raise HTTPException(status_code=400, detail="GSTIN is not valid")
    if result.registration_status.casefold() != "active":
        raise HTTPException(
            status_code=400,
            detail=f"GSTIN registration is {result.registration_status}, not Active",
        )
    if not result.legal_name:
        raise HTTPException(status_code=502, detail="GSTIN record has no legal business name")
    if not result.pan or not hmac.compare_digest(
        pan.pan_fingerprint, _pan_fingerprint(result.pan)
    ):
        raise HTTPException(
            status_code=400,
            detail="GSTIN does not belong to the PAN verified for this merchant account",
        )

    gstin = GstinVerification(
        merchant_id=merchant_id,
        gstin=body.gstin,
        legal_name=result.legal_name,
        business_nature=result.business_nature,
        state_name=result.state_name,
        state_code=result.state_code,
        registration_status=result.registration_status,
        registration_start_date=result.registration_start_date,
        provider_transaction_id=result.transaction_id,
    )
    db.add(gstin)
    await db.flush()
    completed = await _complete_kyc_if_ready(db, ctx, pan, gstin)
    await db.refresh(gstin)
    return _status_payload(
        merchant=ctx.merchant, pan=pan, gstin=gstin, is_kyc_completed=completed
    )


@router.post("/agreements/accept", response_model=MerchantVerificationOut)
async def accept_agreements_and_activate(
    request: Request,
    merchant_id: uuid.UUID,
    body: AgreementAcceptanceRequest,
    db: DBSession,
    ctx: MerchantContext = Depends(require_role("owner")),
) -> MerchantVerificationOut:
    _ensure_matching_merchant(merchant_id, ctx)
    owner_id = ctx.member.user_id
    pan, gstin = await _load_records(db, merchant_id, owner_id)
    kyc_complete = ctx.merchant.is_kyc_completed and pan is not None and gstin is not None
    settings = dict(ctx.merchant.settings or {})
    checks = settings.get("onboarding_checks")
    all_checks_complete = isinstance(checks, dict) and len(checks) == 3 and all(
        checks.get(key) is True
        for key in (
            "authorized_person",
            "business_address",
            "shop_location",
        )
    )
    if not kyc_complete or not all_checks_complete or not settings.get("onboarding_completed"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Complete onboarding and every mandatory verification before activation",
        )

    now = datetime.now(timezone.utc).isoformat()
    settings["agreement_acceptance"] = {
        "accepted": True,
        "accepted_at": now,
        "accepted_by_user_id": str(owner_id),
        "accepted_from_ip": request.client.host if request.client else None,
        "versions": AGREEMENT_VERSIONS,
        "items": body.model_dump(),
    }
    settings["approval_status"] = "approved"
    settings["approved_at"] = now
    ctx.merchant.settings = settings
    await db.commit()
    await db.refresh(ctx.merchant)
    return _status_payload(
        merchant=ctx.merchant,
        pan=pan,
        gstin=gstin,
        is_kyc_completed=True,
    )

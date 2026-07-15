import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from app.models.merchant import Merchant, MerchantMember, MemberRole
from app.models.wallet import Wallet, Transaction, TransactionStatus
from app.models.user import User
from app.schemas.merchant import (
    MemberInvite,
    MemberRoleUpdate,
    MerchantCreate,
    MerchantMemberOut,
    MerchantOut,
    MerchantUpdate,
)
from app.schemas.merchant_product import MerchantProductOut
from app.utils.dependencies import CurrentUser, DBSession
from app.utils.merchant_context import (
    CurrentMerchantContext,
    MerchantContext,
    require_role,
    get_primary_merchant_id,
)
from app.utils.slug import make_unique_slug

router = APIRouter(prefix="/merchants", tags=["merchants"])

_REFERRAL_PREFIX = "SIMULA"


async def _gen_referral_code(db, display_name: str) -> str:
    """Generate a unique referral code of form SIMULA-<NAME>-<YEAR>[-XXXX]."""
    from datetime import datetime
    import secrets
    import string

    name_part = display_name[:12].upper().replace(" ", "")
    base = f"{_REFERRAL_PREFIX}-{name_part}-{datetime.utcnow().year}"

    async def exists(code: str) -> bool:
        res = await db.execute(select(Merchant.id).where(Merchant.referral_code == code))
        return res.scalar_one_or_none() is not None

    if not await exists(base):
        return base
    for _ in range(5):
        suffix = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(4))
        candidate = f"{base}-{suffix}"
        if not await exists(candidate):
            return candidate
    raise RuntimeError("could not generate unique referral code")


async def _gen_partner_id(db) -> str:
    """Generate a unique Merchant Partner ID of form mXXXXXXX (e.g. m1234567)."""
    import random
    import string

    for _ in range(20):
        digits = "".join(random.choices(string.digits, k=7))
        candidate = f"m{digits}"
        res = await db.execute(select(Merchant.id).where(Merchant.partner_id == candidate))
        if res.scalar_one_or_none() is None:
            return candidate
    raise RuntimeError("could not generate unique partner_id")


async def _gen_shop_id(db) -> str:
    """Generate a unique Shop ID of form SXXX (e.g. S123)."""
    import random
    import string

    for _ in range(50):
        digits = "".join(random.choices(string.digits, k=3))
        candidate = f"S{digits}"
        res = await db.execute(select(Merchant.id).where(Merchant.shop_id == candidate))
        if res.scalar_one_or_none() is None:
            return candidate
    # Fallback: use 4 digits if 3-digit space is exhausted
    for _ in range(50):
        digits = "".join(random.choices(string.digits, k=4))
        candidate = f"S{digits}"
        res = await db.execute(select(Merchant.id).where(Merchant.shop_id == candidate))
        if res.scalar_one_or_none() is None:
            return candidate
    raise RuntimeError("could not generate unique shop_id")


async def process_referral_payout(db, merchant: Merchant):
    from decimal import Decimal
    import uuid

    if not merchant.referred_by_code or merchant.referral_bonus_paid:
        return

    # Find referring merchant
    res = await db.execute(select(Merchant).where(Merchant.referral_code == merchant.referred_by_code))
    referrer = res.scalar_one_or_none()
    if not referrer:
        return

    # Credit referrer (credit primary/global wallet)
    primary_referrer_id = await get_primary_merchant_id(db, referrer.id)
    res = await db.execute(select(Wallet).where(Wallet.merchant_id == primary_referrer_id))
    ref_wallet = res.scalar_one_or_none()
    if not ref_wallet:
        ref_wallet = Wallet(merchant_id=primary_referrer_id, balance=Decimal("0.00"))
        db.add(ref_wallet)
        await db.flush()
    ref_wallet.balance += Decimal("500.00")
    
    ref_tx = Transaction(
        merchant_id=referrer.id,
        amount=Decimal("500.00"),
        currency="INR",
        payment_method="referral",
        gateway="system",
        status=TransactionStatus.SUCCESSFUL.value,
        gateway_ref=f"REF-{uuid.uuid4()}"
    )
    db.add(ref_tx)

    # Credit new merchant (credit primary/global wallet)
    primary_merchant_id = await get_primary_merchant_id(db, merchant.id)
    res = await db.execute(select(Wallet).where(Wallet.merchant_id == primary_merchant_id))
    new_wallet = res.scalar_one_or_none()
    if not new_wallet:
        new_wallet = Wallet(merchant_id=primary_merchant_id, balance=Decimal("0.00"))
        db.add(new_wallet)
        await db.flush()
    new_wallet.balance += Decimal("500.00")

    new_tx = Transaction(
        merchant_id=merchant.id,
        amount=Decimal("500.00"),
        currency="INR",
        payment_method="referral",
        gateway="system",
        status=TransactionStatus.SUCCESSFUL.value,
        gateway_ref=f"REF-{uuid.uuid4()}"
    )
    db.add(new_tx)

    # Mark as paid
    merchant.referral_bonus_paid = True
    await db.commit()
    print(f"Processed 500 INR referral payout between new merchant {merchant.id} and referrer {referrer.id}")


async def process_kyc_welcome_bonus(db, merchant: Merchant):
    from decimal import Decimal
    import uuid
    from app.models.wallet import Wallet, Transaction, TransactionStatus
    from app.models.event import LedgerEntry

    if not merchant.is_kyc_completed or merchant.kyc_bonus_paid:
        return

    # Find or create wallet for merchant (use primary/global wallet)
    primary_merchant_id = await get_primary_merchant_id(db, merchant.id)
    res = await db.execute(select(Wallet).where(Wallet.merchant_id == primary_merchant_id))
    wallet = res.scalar_one_or_none()
    if not wallet:
        wallet = Wallet(merchant_id=primary_merchant_id, balance=Decimal("0.00"))
        db.add(wallet)
        await db.flush()

    wallet.balance += Decimal("1000.00")
    
    tx = Transaction(
        merchant_id=merchant.id,
        amount=Decimal("1000.00"),
        currency="INR",
        payment_method="kyc_bonus",
        gateway="system",
        status=TransactionStatus.SUCCESSFUL.value,
        gateway_ref=f"KYC-{uuid.uuid4()}"
    )
    db.add(tx)

    ledger = LedgerEntry(
        merchant_id=merchant.id,
        wallet_id=wallet.id,
        entry_type="credit",
        amount=Decimal("1000.00"),
        reason="kyc_welcome_bonus",
        balance_after=wallet.balance,
        notes="KYC completion welcome bonus"
    )
    db.add(ledger)

    merchant.kyc_bonus_paid = True
    await db.commit()
    print(f"Processed 1000 INR KYC welcome bonus for merchant {merchant.id}")


@router.post("/", response_model=MerchantOut, status_code=status.HTTP_201_CREATED)
async def create_merchant(
    body: MerchantCreate, user: CurrentUser, db: DBSession
) -> Merchant:
    from app.utils.slug import slugify_base

    referred_by_code = None
    if body.referred_by_code:
        res = await db.execute(select(Merchant).where(Merchant.referral_code == body.referred_by_code.upper()))
        referrer = res.scalar_one_or_none()
        if not referrer:
            raise HTTPException(status_code=400, detail="Invalid referral code. No such merchant exists.")
        referred_by_code = body.referred_by_code.upper()

    base = slugify_base(body.legal_name)
    res = await db.execute(select(Merchant.slug).where(Merchant.slug.like(f"{base}%")))
    existing_slugs = set(res.scalars().all())
    slug = make_unique_slug(body.legal_name, lambda s: s in existing_slugs)

    referral = await _gen_referral_code(db, body.display_name)
    shop_id = await _gen_shop_id(db)

    # Fetch existing shops owned by this user (oldest first = the "primary").
    existing_merchants_res = await db.execute(
        select(Merchant)
        .join(MerchantMember, MerchantMember.merchant_id == Merchant.id)
        .where(MerchantMember.user_id == user.id, MerchantMember.role == MemberRole.OWNER.value)
        .order_by(Merchant.created_at.asc())
    )
    existing_merchants = existing_merchants_res.scalars().all()

    inherited_settings = {}
    is_kyc_completed = False
    kyc_bonus_paid = False
    primary_m = existing_merchants[0] if existing_merchants else None

    if primary_m is not None:
        inherited_settings = primary_m.settings.copy() if primary_m.settings else {}
        is_kyc_completed = primary_m.is_kyc_completed
        kyc_bonus_paid = primary_m.kyc_bonus_paid

    # Partner hierarchy: every shop owned by the same user shares ONE partner_id
    # (the owner/partner), while each shop keeps its own unique shop_id. Only the
    # owner's first shop mints a fresh partner_id; later shops inherit it.
    if primary_m is not None and primary_m.partner_id:
        partner_id = primary_m.partner_id
    else:
        partner_id = await _gen_partner_id(db)

    # Merge body settings if provided
    final_settings = inherited_settings
    if body.settings:
        final_settings.update(body.settings)

    merchant = Merchant(
        partner_id=partner_id,
        shop_id=shop_id,
        slug=slug,
        legal_name=body.legal_name,
        display_name=body.display_name,
        country=body.country,
        support_email=body.support_email or (primary_m.support_email if existing_merchants else None),
        support_phone=body.support_phone or (primary_m.support_phone if existing_merchants else None),
        logo_url=body.logo_url or (primary_m.logo_url if existing_merchants else None),
        settings=final_settings,
        referral_code=referral,
        # Location is set once at creation — immutable thereafter
        address=body.address,
        latitude=body.latitude,
        longitude=body.longitude,
        range_km=body.range_km,
        referred_by_code=referred_by_code,
        is_kyc_completed=is_kyc_completed,
        referral_bonus_paid=False,
        kyc_bonus_paid=kyc_bonus_paid,
    )
    db.add(merchant)
    await db.flush()

    membership = MerchantMember(
        merchant_id=merchant.id, user_id=user.id, role=MemberRole.OWNER.value
    )
    db.add(membership)

    # Phase 3: auto-create wallet for new merchant
    wallet = Wallet(merchant_id=merchant.id)
    db.add(wallet)

    await db.commit()
    await db.refresh(merchant)
    return merchant


@router.get("/me", response_model=list[MerchantOut])
async def list_my_merchants(user: CurrentUser, db: DBSession) -> list[Merchant]:
    res = await db.execute(
        select(Merchant)
        .join(MerchantMember, MerchantMember.merchant_id == Merchant.id)
        .where(MerchantMember.user_id == user.id)
        .order_by(Merchant.created_at.desc())
    )
    return list(res.scalars().all())


@router.get("/{merchant_id}", response_model=MerchantOut)
async def get_merchant(merchant_id: uuid.UUID, ctx: CurrentMerchantContext) -> Merchant:
    if ctx.merchant.id != merchant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Merchant-Id header must match path merchant_id",
        )
    return ctx.merchant


@router.patch("/{merchant_id}", response_model=MerchantOut)
async def update_merchant(
    merchant_id: uuid.UUID,
    body: MerchantUpdate,
    db: DBSession,
    ctx: MerchantContext = Depends(require_role("owner", "admin")),
) -> Merchant:
    if ctx.merchant.id != merchant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Merchant-Id header must match path merchant_id",
        )
    data = body.model_dump(exclude_unset=True)

    # Location fields are immutable after creation — silently remove them if
    # accidentally sent. Callers should direct merchants to email support for changes.
    for loc_field in ("address", "latitude", "longitude"):
        data.pop(loc_field, None)

    kyc_transition = False
    if "is_kyc_completed" in data and data["is_kyc_completed"] is True and not ctx.merchant.is_kyc_completed:
        kyc_transition = True

    for k, v in data.items():
        setattr(ctx.merchant, k, v)

    await db.commit()

    if kyc_transition:
        await process_referral_payout(db, ctx.merchant)
        await process_kyc_welcome_bonus(db, ctx.merchant)

    await db.refresh(ctx.merchant)
    return ctx.merchant


@router.get("/{merchant_id}/referrals", response_model=list[MerchantOut])
async def get_referred_merchants(
    merchant_id: uuid.UUID,
    db: DBSession,
    ctx: CurrentMerchantContext
) -> list[Merchant]:
    """Fetch all merchants referred by this merchant."""
    if ctx.merchant.id != merchant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Merchant-Id header must match path merchant_id",
        )
    res = await db.execute(
        select(Merchant)
        .where(Merchant.referred_by_code == ctx.merchant.referral_code)
        .order_by(Merchant.created_at.desc())
    )
    return list(res.scalars().all())


def _member_to_out(member: MerchantMember, user: User) -> dict:
    return {
        "id": member.id,
        "user_id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "role": member.role,
        "joined_at": member.joined_at,
    }


@router.get("/{merchant_id}/members", response_model=list[MerchantMemberOut])
async def list_members(
    merchant_id: uuid.UUID, db: DBSession, ctx: CurrentMerchantContext
) -> list[dict]:
    if ctx.merchant.id != merchant_id:
        raise HTTPException(status_code=400, detail="merchant_id mismatch")
    res = await db.execute(
        select(MerchantMember, User)
        .join(User, User.id == MerchantMember.user_id)
        .where(MerchantMember.merchant_id == merchant_id)
        .order_by(MerchantMember.joined_at.asc())
    )
    return [_member_to_out(member, user) for member, user in res.all()]


@router.post(
    "/{merchant_id}/members/invite",
    response_model=MerchantMemberOut,
    status_code=status.HTTP_201_CREATED,
)
async def invite_member(
    merchant_id: uuid.UUID,
    body: MemberInvite,
    db: DBSession,
    ctx: MerchantContext = Depends(require_role("owner", "admin")),
) -> dict:
    if ctx.merchant.id != merchant_id:
        raise HTTPException(status_code=400, detail="merchant_id mismatch")

    res = await db.execute(select(User).where(User.email == body.email.lower()))
    invitee = res.scalar_one_or_none()
    if not invitee:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="user with that email not found (must sign up first)",
        )

    # Conflict if already a member
    res = await db.execute(
        select(MerchantMember).where(
            MerchantMember.merchant_id == merchant_id,
            MerchantMember.user_id == invitee.id,
        )
    )
    if res.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="already a member")

    membership = MerchantMember(
        merchant_id=merchant_id,
        user_id=invitee.id,
        role=body.role,
        invited_by=ctx.member.user_id,
    )
    db.add(membership)
    await db.commit()
    await db.refresh(membership)
    return _member_to_out(membership, invitee)


@router.patch("/{merchant_id}/members/{user_id}", response_model=MerchantMemberOut)
async def change_member_role(
    merchant_id: uuid.UUID,
    user_id: uuid.UUID,
    body: MemberRoleUpdate,
    db: DBSession,
    ctx: MerchantContext = Depends(require_role("owner")),
) -> dict:
    if ctx.merchant.id != merchant_id:
        raise HTTPException(status_code=400, detail="merchant_id mismatch")

    res = await db.execute(
        select(MerchantMember, User)
        .join(User, User.id == MerchantMember.user_id)
        .where(
            MerchantMember.merchant_id == merchant_id, MerchantMember.user_id == user_id
        )
    )
    row = res.first()
    if not row:
        raise HTTPException(status_code=404, detail="member not found")
    member, user = row
    member.role = body.role
    await db.commit()
    await db.refresh(member)
    return _member_to_out(member, user)


@router.delete("/{merchant_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    merchant_id: uuid.UUID,
    user_id: uuid.UUID,
    db: DBSession,
    ctx: MerchantContext = Depends(require_role("owner")),
) -> None:
    if ctx.merchant.id != merchant_id:
        raise HTTPException(status_code=400, detail="merchant_id mismatch")
    if ctx.member.user_id == user_id:
        raise HTTPException(
            status_code=400, detail="cannot remove yourself; transfer ownership first"
        )

    res = await db.execute(
        select(MerchantMember).where(
            MerchantMember.merchant_id == merchant_id, MerchantMember.user_id == user_id
        )
    )
    member = res.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=404, detail="member not found")
    await db.delete(member)
    await db.commit()


@router.get("/public/nearby", response_model=list[MerchantOut])
async def get_nearby_merchants(
    db: DBSession,
    lat: float | None = None,
    lon: float | None = None,
    category: str | None = None,
    limit: int = 20,
) -> list[Merchant]:
    """Discover merchants for the Shop screen.

    Returns active merchants, optionally ordered by proximity to the provided lat/lon coordinates.
    """
    stmt = (
        select(Merchant)
        .where(Merchant.status != "suspended")
    )
    res = await db.execute(stmt)
    merchants = list(res.scalars().all())

    if lat is not None and lon is not None:
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

        # Attach calculated distance to each merchant object
        for merchant in merchants:
            if merchant.latitude is not None and merchant.longitude is not None:
                merchant.distance = calculate_distance(
                    lat, lon, merchant.latitude, merchant.longitude
                )
            else:
                merchant.distance = float("inf")

        # Filter by service range restriction
        filtered_merchants = []
        for merchant in merchants:
            if merchant.range_km is not None and merchant.distance != float("inf"):
                if merchant.distance > merchant.range_km:
                    continue
            filtered_merchants.append(merchant)
        merchants = filtered_merchants

        # Sort by distance
        merchants.sort(key=lambda m: m.distance)
    else:
        # Otherwise sort by newest first (default fallback)
        from datetime import datetime
        for merchant in merchants:
            merchant.distance = None
        merchants.sort(key=lambda m: m.created_at or datetime.min, reverse=True)

    return merchants[:max(1, min(limit, 50))]


@router.get("/public/{lookup_value}", response_model=MerchantOut)
async def get_public_merchant(
    lookup_value: str,
    db: DBSession,
) -> Merchant:
    """Fetch public merchant details by UUID, slug, or referral code."""
    try:
        merchant_id = uuid.UUID(lookup_value)
        stmt = select(Merchant).where(Merchant.id == merchant_id)
    except ValueError:
        stmt = select(Merchant).where(
            (Merchant.slug == lookup_value) | (Merchant.referral_code == lookup_value)
        )
    res = await db.execute(stmt)
    merchant = res.scalar_one_or_none()
    if not merchant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Merchant not found")
    if merchant.status == "suspended":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Merchant account is suspended"
        )
    return merchant


@router.get("/public/{lookup_value}/products", response_model=list[MerchantProductOut])
async def get_public_merchant_products(
    lookup_value: str,
    db: DBSession,
) -> list:
    """Fetch published merchant products by merchant UUID, slug, or referral code."""
    from sqlalchemy.orm import selectinload
    from app.models.merchant_product import MerchantProduct

    try:
        merchant_id = uuid.UUID(lookup_value)
        stmt = select(Merchant.id).where(Merchant.id == merchant_id)
    except ValueError:
        stmt = select(Merchant.id).where(
            (Merchant.slug == lookup_value) | (Merchant.referral_code == lookup_value)
        )
    m_res = await db.execute(stmt)
    m_id = m_res.scalar_one_or_none()
    if not m_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Merchant not found")

    p_stmt = (
        select(MerchantProduct)
        .options(selectinload(MerchantProduct.variants))
        .where(
            MerchantProduct.merchant_id == m_id,
            MerchantProduct.status == "published",
        )
        .order_by(MerchantProduct.created_at.desc())
    )
    res = await db.execute(p_stmt)
    return list(res.scalars().all())




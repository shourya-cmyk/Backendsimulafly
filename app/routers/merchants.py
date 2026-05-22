import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from app.models.merchant import Merchant, MerchantMember, MemberRole
from app.models.user import User
from app.schemas.merchant import (
    MerchantCreate,
    MerchantOut,
    MerchantUpdate,
)
from app.utils.dependencies import CurrentUser, DBSession
from app.utils.merchant_context import (
    CurrentMerchantContext,
    MerchantContext,
    require_role,
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


@router.post("/", response_model=MerchantOut, status_code=status.HTTP_201_CREATED)
async def create_merchant(
    body: MerchantCreate, user: CurrentUser, db: DBSession
) -> Merchant:
    from app.utils.slug import slugify_base

    base = slugify_base(body.legal_name)
    res = await db.execute(select(Merchant.slug).where(Merchant.slug.like(f"{base}%")))
    existing_slugs = set(res.scalars().all())
    slug = make_unique_slug(body.legal_name, lambda s: s in existing_slugs)

    referral = await _gen_referral_code(db, body.display_name)

    merchant = Merchant(
        slug=slug,
        legal_name=body.legal_name,
        display_name=body.display_name,
        country=body.country,
        support_email=body.support_email,
        support_phone=body.support_phone,
        referral_code=referral,
    )
    db.add(merchant)
    await db.flush()

    membership = MerchantMember(
        merchant_id=merchant.id, user_id=user.id, role=MemberRole.OWNER.value
    )
    db.add(membership)
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
    for k, v in data.items():
        setattr(ctx.merchant, k, v)
    await db.commit()
    await db.refresh(ctx.merchant)
    return ctx.merchant

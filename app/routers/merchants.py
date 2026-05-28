import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from app.models.merchant import Merchant, MerchantMember, MemberRole
from app.models.wallet import Wallet
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
    for k, v in data.items():
        setattr(ctx.merchant, k, v)
    await db.commit()
    await db.refresh(ctx.merchant)
    return ctx.merchant


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
        .options(selectinload(MerchantProduct.external_links), selectinload(MerchantProduct.variants))
        .where(
            MerchantProduct.merchant_id == m_id,
            MerchantProduct.status == "published",
        )
        .order_by(MerchantProduct.created_at.desc())
    )
    res = await db.execute(p_stmt)
    return list(res.scalars().all())


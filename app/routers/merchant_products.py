import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.models.merchant_product import MerchantProduct
from app.schemas.merchant_product import (
    MerchantProductCreate,
    MerchantProductOut,
    MerchantProductUpdate,
)
from app.utils.dependencies import DBSession
from app.utils.merchant_context import CurrentMerchantContext

router = APIRouter(prefix="/merchant/products", tags=["merchant-products"])


class PaginatedProducts(BaseModel):
    items: list[MerchantProductOut]
    total: int
    limit: int
    offset: int


@router.get("/", response_model=PaginatedProducts)
async def list_products(
    db: DBSession,
    ctx: CurrentMerchantContext,
    status_filter: str | None = Query(default=None, alias="status"),
    search: str | None = Query(default=None, min_length=1, max_length=200),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict:
    base = select(MerchantProduct).where(MerchantProduct.merchant_id == ctx.merchant.id)
    count_base = select(func.count()).select_from(MerchantProduct).where(
        MerchantProduct.merchant_id == ctx.merchant.id
    )

    if status_filter:
        base = base.where(MerchantProduct.status == status_filter)
        count_base = count_base.where(MerchantProduct.status == status_filter)
    if search:
        pat = f"%{search}%"
        base = base.where(
            or_(MerchantProduct.title.ilike(pat), MerchantProduct.sku.ilike(pat))
        )
        count_base = count_base.where(
            or_(MerchantProduct.title.ilike(pat), MerchantProduct.sku.ilike(pat))
        )

    total_res = await db.execute(count_base)
    total = total_res.scalar_one()

    base = (
        base.options(selectinload(MerchantProduct.external_links))
        .order_by(MerchantProduct.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    rows = (await db.execute(base)).scalars().all()
    return {"items": list(rows), "total": total, "limit": limit, "offset": offset}


@router.post("/", response_model=MerchantProductOut, status_code=status.HTTP_201_CREATED)
async def create_product(
    body: MerchantProductCreate,
    db: DBSession,
    ctx: CurrentMerchantContext,
    background_tasks: BackgroundTasks,
) -> MerchantProduct:
    product = MerchantProduct(
        merchant_id=ctx.merchant.id,
        **body.model_dump(),
    )
    db.add(product)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="a product with that SKU already exists for this merchant",
        )
    # Reload with external_links eagerly to avoid lazy-load greenlet errors
    stmt = (
        select(MerchantProduct)
        .options(selectinload(MerchantProduct.external_links))
        .where(MerchantProduct.id == product.id)
    )
    product = (await db.execute(stmt)).scalar_one()

    # Schedule embedding regen (non-blocking)
    from app.services.embedding import regenerate_embedding
    background_tasks.add_task(regenerate_embedding, db, product.id)

    return product


@router.get("/{product_id}", response_model=MerchantProductOut)
async def get_product(
    product_id: uuid.UUID, db: DBSession, ctx: CurrentMerchantContext
) -> MerchantProduct:
    stmt = (
        select(MerchantProduct)
        .options(selectinload(MerchantProduct.external_links))
        .where(
            MerchantProduct.id == product_id,
            MerchantProduct.merchant_id == ctx.merchant.id,
        )
    )
    product = (await db.execute(stmt)).scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="product not found")
    return product


def _embedding_fields_changed(body: MerchantProductUpdate, current: MerchantProduct) -> bool:
    data = body.model_dump(exclude_unset=True)
    for key in ("title", "description", "category"):
        if key in data and data[key] != getattr(current, key):
            return True
    return False


@router.patch("/{product_id}", response_model=MerchantProductOut)
async def update_product(
    product_id: uuid.UUID,
    body: MerchantProductUpdate,
    db: DBSession,
    ctx: CurrentMerchantContext,
    background_tasks: BackgroundTasks,
) -> MerchantProduct:
    stmt = (
        select(MerchantProduct)
        .options(selectinload(MerchantProduct.external_links))
        .where(
            MerchantProduct.id == product_id,
            MerchantProduct.merchant_id == ctx.merchant.id,
        )
    )
    product = (await db.execute(stmt)).scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="product not found")

    needs_embedding_regen = _embedding_fields_changed(body, product)

    data = body.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(product, k, v)

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="conflict (duplicate SKU?)")

    # Re-fetch with eager-loaded external_links to avoid MissingGreenlet on serialization.
    stmt = (
        select(MerchantProduct)
        .options(selectinload(MerchantProduct.external_links))
        .where(MerchantProduct.id == product_id)
    )
    product = (await db.execute(stmt)).scalar_one()

    if needs_embedding_regen:
        from app.services.embedding import regenerate_embedding
        background_tasks.add_task(regenerate_embedding, db, product.id)

    return product


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_product(
    product_id: uuid.UUID, db: DBSession, ctx: CurrentMerchantContext
) -> None:
    product = await db.get(MerchantProduct, product_id)
    if not product or product.merchant_id != ctx.merchant.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="product not found")
    product.status = "archived"
    await db.commit()


@router.post("/{product_id}/publish", response_model=MerchantProductOut)
async def publish_product(
    product_id: uuid.UUID, db: DBSession, ctx: CurrentMerchantContext
) -> MerchantProduct:
    stmt = (
        select(MerchantProduct)
        .options(selectinload(MerchantProduct.external_links))
        .where(
            MerchantProduct.id == product_id,
            MerchantProduct.merchant_id == ctx.merchant.id,
        )
    )
    product = (await db.execute(stmt)).scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="product not found")

    if product.status == "archived":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="archived products cannot be published; create a new one",
        )

    product.status = "published"
    # NOTE: Phase 3 will add a wallet-balance check here.
    await db.commit()

    # Re-fetch for serialization
    stmt = (
        select(MerchantProduct)
        .options(selectinload(MerchantProduct.external_links))
        .where(MerchantProduct.id == product_id)
    )
    product = (await db.execute(stmt)).scalar_one()
    return product


from app.models.merchant_product import MerchantProductExternalLink
from app.schemas.merchant_product import (
    ExternalLinkCreate,
    ExternalLinkOut,
    ExternalLinkUpdate,
)


async def _product_owned(
    db: DBSession, product_id: uuid.UUID, merchant_id: uuid.UUID
) -> MerchantProduct:
    """Fetch a product or raise 404 if missing / owned by another merchant."""
    p = await db.get(MerchantProduct, product_id)
    if not p or p.merchant_id != merchant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="product not found")
    return p


@router.post(
    "/{product_id}/external-links/",
    response_model=ExternalLinkOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_external_link(
    product_id: uuid.UUID,
    body: ExternalLinkCreate,
    db: DBSession,
    ctx: CurrentMerchantContext,
) -> MerchantProductExternalLink:
    await _product_owned(db, product_id, ctx.merchant.id)

    link = MerchantProductExternalLink(
        merchant_product_id=product_id,
        **body.model_dump(mode="json"),
    )
    db.add(link)
    await db.commit()
    await db.refresh(link)
    return link


@router.patch(
    "/{product_id}/external-links/{link_id}",
    response_model=ExternalLinkOut,
)
async def update_external_link(
    product_id: uuid.UUID,
    link_id: uuid.UUID,
    body: ExternalLinkUpdate,
    db: DBSession,
    ctx: CurrentMerchantContext,
) -> MerchantProductExternalLink:
    await _product_owned(db, product_id, ctx.merchant.id)

    link = await db.get(MerchantProductExternalLink, link_id)
    if not link or link.merchant_product_id != product_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="link not found")

    data = body.model_dump(exclude_unset=True, mode="json")
    for k, v in data.items():
        setattr(link, k, v)
    await db.commit()
    await db.refresh(link)
    return link


@router.delete(
    "/{product_id}/external-links/{link_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_external_link(
    product_id: uuid.UUID,
    link_id: uuid.UUID,
    db: DBSession,
    ctx: CurrentMerchantContext,
) -> None:
    await _product_owned(db, product_id, ctx.merchant.id)

    link = await db.get(MerchantProductExternalLink, link_id)
    if not link or link.merchant_product_id != product_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="link not found")
    await db.delete(link)
    await db.commit()

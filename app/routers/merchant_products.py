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

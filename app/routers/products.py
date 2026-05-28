"""Consumer-facing products endpoints — switched to merchant_products (Phase 4)."""
import uuid

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select, text
from sqlalchemy.orm import selectinload

from app.models.merchant_product import MerchantProduct
from app.schemas.merchant_product import MerchantProductOut
from app.services.llm import get_embeddings
from app.services.rag_service import _vector_literal
from app.utils.dependencies import CurrentUser, DBSession

router = APIRouter(prefix="/products", tags=["products"])


@router.get("/search", response_model=list[MerchantProductOut])
async def search(
    user: CurrentUser,
    db: DBSession,
    q: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(default=10, ge=1, le=50),
):
    """Semantic search over published merchant products via pgvector HNSW."""
    embedding = await get_embeddings().aembed_query(q)
    # Use halfvec cast on both sides — index is on (embedding::halfvec(3072)) halfvec_cosine_ops
    sql = text(
        "SELECT id FROM merchant_products "
        "WHERE embedding IS NOT NULL AND status='published' "
        "ORDER BY embedding::halfvec(3072) <=> CAST(:q AS halfvec(3072)) LIMIT :k"
    )
    res = await db.execute(sql, {"q": _vector_literal(embedding), "k": limit})
    ids = [row[0] for row in res.fetchall()]
    if not ids:
        return []
    rows = await db.execute(
        select(MerchantProduct)
        .options(selectinload(MerchantProduct.external_links))
        .where(MerchantProduct.id.in_(ids))
    )
    by_id = {p.id: p for p in rows.scalars().all()}
    return [by_id[i] for i in ids if i in by_id]


@router.get("/", response_model=list[MerchantProductOut])
async def list_products(
    user: CurrentUser,
    db: DBSession,
    category: str | None = Query(default=None, max_length=255),
    max_price: float | None = Query(default=None, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    stmt = (
        select(MerchantProduct)
        .options(selectinload(MerchantProduct.external_links))
        .where(MerchantProduct.status == "published")
    )
    if category:
        stmt = stmt.where(MerchantProduct.category.ilike(f"%{category}%"))
    if max_price is not None:
        stmt = stmt.where(MerchantProduct.in_app_price <= max_price)
    stmt = stmt.order_by(MerchantProduct.created_at.desc()).offset(offset).limit(limit)
    res = await db.execute(stmt)
    return list(res.scalars().all())


@router.get("/{product_id}", response_model=MerchantProductOut)
async def get_product(product_id: uuid.UUID, user: CurrentUser, db: DBSession):
    stmt = (
        select(MerchantProduct)
        .options(selectinload(MerchantProduct.external_links))
        .where(
            MerchantProduct.id == product_id,
            MerchantProduct.status == "published",
        )
    )
    product = (await db.execute(stmt)).scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="product not found")
    return product

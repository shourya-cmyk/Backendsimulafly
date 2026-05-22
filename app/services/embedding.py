"""Background embedding regeneration for MerchantProduct.

Called as a FastAPI BackgroundTasks callback after create/update of a product
whose title/description/category changed. The actual embedding API call is
isolated behind `_get_embeddings_client()` to make mocking easy.
"""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.merchant_product import MerchantProduct

log = get_logger("app.services.embedding")


def compose_embedding_text(
    *, title: str, description: str | None, category: str | None
) -> str:
    """Concatenate the fields RAG should index. Skips None values cleanly."""
    parts = [title]
    if category:
        parts.append(f"Category: {category}")
    if description:
        parts.append(description)
    return ". ".join(parts)


def _get_embeddings_client():
    """Resolve the embeddings client lazily so tests can patch this function."""
    from app.services.llm import get_embeddings
    return get_embeddings()


async def regenerate_embedding(db: AsyncSession, product_id: uuid.UUID) -> None:
    """Fetch the product, compute embedding from title+desc+category, save back.

    Errors are logged but not raised — embedding is non-critical to product save.
    """
    product = await db.get(MerchantProduct, product_id)
    if not product:
        log.warning("regen_embedding_product_not_found", product_id=str(product_id))
        return

    text = compose_embedding_text(
        title=product.title,
        description=product.description,
        category=product.category,
    )

    try:
        client = _get_embeddings_client()
        vector = await client.aembed_query(text)
        product.embedding = vector
        await db.commit()
        log.info("regen_embedding_done", product_id=str(product_id), dims=len(vector))
    except Exception as e:  # noqa: BLE001
        log.warning("regen_embedding_failed", product_id=str(product_id), error=str(e))
        await db.rollback()

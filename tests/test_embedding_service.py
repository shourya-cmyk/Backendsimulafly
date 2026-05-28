import uuid
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_compose_embedding_text_uses_title_description_category():
    from app.services.embedding import compose_embedding_text

    text = compose_embedding_text(
        title="Oak Dining Table",
        description="A handcrafted solid oak table seating six.",
        category="Furniture",
    )

    # All three fields appear in the composed text
    assert "Oak Dining Table" in text
    assert "handcrafted solid oak" in text
    assert "Furniture" in text


@pytest.mark.asyncio
async def test_compose_embedding_text_handles_missing_fields():
    from app.services.embedding import compose_embedding_text

    text = compose_embedding_text(title="Just Title", description=None, category=None)
    assert "Just Title" in text
    # No 'None' string artifact
    assert "None" not in text


@pytest.mark.asyncio
async def test_regenerate_embedding_writes_vector_to_product(db_session, test_user):
    from app.models.merchant import Merchant, MerchantMember, MemberRole
    from app.models.merchant_product import MerchantProduct
    from app.services import embedding as emb_module

    m = Merchant(slug="emb", legal_name="E", display_name="E", referral_code="E-1")
    db_session.add(m)
    await db_session.commit()
    await db_session.refresh(m)
    db_session.add(MerchantMember(merchant_id=m.id, user_id=test_user.id, role=MemberRole.OWNER.value))
    await db_session.commit()

    p = MerchantProduct(
        merchant_id=m.id, sku="EMB-1", title="Oak Table", description="Solid oak.", category="Furniture"
    )
    db_session.add(p)
    await db_session.commit()
    await db_session.refresh(p)
    assert p.embedding is None

    # Mock the embeddings client to return a fixed vector
    fake_vec = [0.1] * 3072
    fake_client = AsyncMock()
    fake_client.aembed_query.return_value = fake_vec

    with patch.object(emb_module, "_get_embeddings_client", return_value=fake_client):
        await emb_module.regenerate_embedding(db_session, p.id)

    await db_session.refresh(p)
    assert p.embedding is not None
    assert len(p.embedding) == 3072
    assert p.embedding[0] == pytest.approx(0.1)

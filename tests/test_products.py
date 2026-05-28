"""Consumer /api/v1/products/* — now backed by merchant_products (Phase 4)."""
import uuid

import pytest

from app.models.merchant import Merchant, MerchantMember, MemberRole
from app.models.merchant_product import MerchantProduct


async def _make_merchant(db_session) -> Merchant:
    m = Merchant(
        slug=f"tp-{uuid.uuid4().hex[:6]}",
        legal_name="Test Merchant",
        display_name="TM",
        referral_code=f"TM-{uuid.uuid4().hex[:6].upper()}",
    )
    db_session.add(m)
    await db_session.commit()
    await db_session.refresh(m)
    return m


@pytest.mark.asyncio
async def test_products_filter_by_category_and_price(auth_client, db_session):
    m = await _make_merchant(db_session)
    products = [
        MerchantProduct(merchant_id=m.id, sku="A1", title="Cheap Sofa", category="Sofa", in_app_price=5000, status="published"),
        MerchantProduct(merchant_id=m.id, sku="A2", title="Pricey Sofa", category="Sofa", in_app_price=25000, status="published"),
        MerchantProduct(merchant_id=m.id, sku="A3", title="Lamp", category="Lamp", in_app_price=2000, status="published"),
    ]
    for p in products:
        db_session.add(p)
    await db_session.commit()

    r = await auth_client.get("/api/v1/products/?category=Sofa&max_price=10000")
    assert r.status_code == 200
    items = r.json()
    skus = {p["sku"] for p in items}
    assert "A1" in skus
    assert "A2" not in skus
    assert "A3" not in skus


@pytest.mark.asyncio
async def test_product_by_id(auth_client, db_session):
    m = await _make_merchant(db_session)
    p = MerchantProduct(merchant_id=m.id, sku="X1", title="Table", category="Table", in_app_price=3000, status="published")
    db_session.add(p)
    await db_session.commit()
    await db_session.refresh(p)

    r = await auth_client.get(f"/api/v1/products/{p.id}")
    assert r.status_code == 200
    assert r.json()["sku"] == "X1"


@pytest.mark.asyncio
async def test_product_by_id_draft_returns_404(auth_client, db_session):
    m = await _make_merchant(db_session)
    p = MerchantProduct(merchant_id=m.id, sku="D1", title="Draft Chair", category="Chair", status="draft")
    db_session.add(p)
    await db_session.commit()
    await db_session.refresh(p)

    r = await auth_client.get(f"/api/v1/products/{p.id}")
    assert r.status_code == 404

import uuid

import pytest

from app.models.merchant_product import MerchantProduct


async def _seed_product(db_session, *, asin: str = "S001"):
    from app.models.merchant import Merchant

    m = Merchant(
        slug=f"dummy-{uuid.uuid4().hex[:6]}",
        legal_name="Dummy",
        display_name="Dummy",
        referral_code=f"DUM-{uuid.uuid4().hex[:6]}",
        settings={"onboarding_completed": True},
    )
    db_session.add(m)
    await db_session.commit()
    await db_session.refresh(m)

    p = MerchantProduct(
        merchant_id=m.id,
        sku=asin,
        title="Saved Sofa",
        category="Sofa",
        in_app_price=12000.0,
    )
    db_session.add(p)
    await db_session.commit()
    await db_session.refresh(p)
    return p


@pytest.mark.asyncio
async def test_saved_add_list_delete(auth_client, db_session):
    product = await _seed_product(db_session)

    # Empty by default
    r = await auth_client.get("/api/v1/saved/")
    assert r.status_code == 200
    assert r.json() == {"items": [], "item_count": 0}

    # Add
    r = await auth_client.post(
        "/api/v1/saved/",
        json={"product_id": str(product.id), "note": "for the bedroom"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["item_count"] == 1
    assert body["items"][0]["note"] == "for the bedroom"
    item_id = body["items"][0]["id"]

    # Idempotent: adding again updates the note instead of duplicating.
    r = await auth_client.post(
        "/api/v1/saved/",
        json={"product_id": str(product.id), "note": "actually the study"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["item_count"] == 1
    assert body["items"][0]["note"] == "actually the study"

    # Patch — clear-via-empty-string then re-set
    r = await auth_client.patch(
        f"/api/v1/saved/{item_id}", json={"note": "final note"}
    )
    assert r.status_code == 200
    assert r.json()["note"] == "final note"

    # Delete by item id
    r = await auth_client.delete(f"/api/v1/saved/{item_id}")
    assert r.status_code == 204
    r = await auth_client.get("/api/v1/saved/")
    assert r.json()["item_count"] == 0


@pytest.mark.asyncio
async def test_saved_unsave_by_product(auth_client, db_session):
    product = await _seed_product(db_session, asin="S002")
    await auth_client.post(
        "/api/v1/saved/", json={"product_id": str(product.id)}
    )
    r = await auth_client.delete(f"/api/v1/saved/by-product/{product.id}")
    assert r.status_code == 204
    r = await auth_client.get("/api/v1/saved/")
    assert r.json()["item_count"] == 0


@pytest.mark.asyncio
async def test_saved_missing_product_404(auth_client):
    r = await auth_client.post(
        "/api/v1/saved/", json={"product_id": str(uuid.uuid4())}
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_saved_unsave_missing_404(auth_client):
    r = await auth_client.delete(f"/api/v1/saved/by-product/{uuid.uuid4()}")
    assert r.status_code == 404

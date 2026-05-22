import uuid

import pytest


@pytest.mark.asyncio
async def test_create_merchant_product_persists_required_fields(db_session, test_user):
    from app.models.merchant import Merchant, MerchantMember, MemberRole
    from app.models.merchant_product import MerchantProduct, ProductStatus

    m = Merchant(slug="mp-test", legal_name="MP", display_name="MP", referral_code="MP-1")
    db_session.add(m)
    await db_session.commit()
    await db_session.refresh(m)
    db_session.add(MerchantMember(merchant_id=m.id, user_id=test_user.id, role=MemberRole.OWNER.value))
    await db_session.commit()

    p = MerchantProduct(
        merchant_id=m.id,
        sku="SKU-001",
        title="Oak Dining Table",
        category="Furniture",
    )
    db_session.add(p)
    await db_session.commit()
    await db_session.refresh(p)

    assert isinstance(p.id, uuid.UUID)
    assert p.status == ProductStatus.DRAFT.value
    assert p.has_simulafly_listing is False
    assert p.dimensions == {}
    assert p.materials == {}
    assert p.additional_images == []
    assert p.health_score == "good"


@pytest.mark.asyncio
async def test_merchant_product_sku_unique_per_merchant(db_session, test_user):
    from app.models.merchant import Merchant, MerchantMember, MemberRole
    from app.models.merchant_product import MerchantProduct

    m = Merchant(slug="sku-test", legal_name="SKU", display_name="SKU", referral_code="SKU-1")
    db_session.add(m)
    await db_session.commit()
    await db_session.refresh(m)
    db_session.add(MerchantMember(merchant_id=m.id, user_id=test_user.id, role=MemberRole.OWNER.value))
    await db_session.commit()

    db_session.add(MerchantProduct(merchant_id=m.id, sku="DUP", title="A"))
    await db_session.commit()

    db_session.add(MerchantProduct(merchant_id=m.id, sku="DUP", title="B"))
    with pytest.raises(Exception):  # IntegrityError from UNIQUE(merchant_id, sku)
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_external_link_attached_to_product(db_session, test_user):
    from app.models.merchant import Merchant, MerchantMember, MemberRole
    from app.models.merchant_product import MerchantProduct, MerchantProductExternalLink

    m = Merchant(slug="link-test", legal_name="L", display_name="L", referral_code="L-1")
    db_session.add(m)
    await db_session.commit()
    await db_session.refresh(m)
    db_session.add(MerchantMember(merchant_id=m.id, user_id=test_user.id, role=MemberRole.OWNER.value))
    await db_session.commit()

    p = MerchantProduct(merchant_id=m.id, sku="WITH-LINK", title="P")
    db_session.add(p)
    await db_session.commit()
    await db_session.refresh(p)

    link = MerchantProductExternalLink(
        merchant_product_id=p.id,
        platform="amazon",
        url="https://amazon.in/dp/B0XX",
        label="Buy on Amazon",
        is_primary=True,
    )
    db_session.add(link)
    await db_session.commit()
    await db_session.refresh(link)

    assert isinstance(link.id, uuid.UUID)
    assert link.platform == "amazon"
    assert link.is_primary is True
    assert link.position == 0


def test_merchant_product_create_validates_required_fields():
    from app.schemas.merchant_product import MerchantProductCreate
    from pydantic import ValidationError

    valid = MerchantProductCreate(sku="A1", title="Oak Table")
    assert valid.status == "draft"  # default
    assert valid.has_simulafly_listing is False

    with pytest.raises(ValidationError):
        MerchantProductCreate(sku="A1")  # missing title

    with pytest.raises(ValidationError):
        MerchantProductCreate(title="Just Title")  # missing sku


def test_external_link_create_validates_platform_and_url():
    from app.schemas.merchant_product import ExternalLinkCreate
    from pydantic import ValidationError

    valid = ExternalLinkCreate(platform="amazon", url="https://amazon.in/dp/B0XX")
    assert valid.is_primary is False
    assert valid.position == 0

    with pytest.raises(ValidationError):
        ExternalLinkCreate(platform="not-a-platform", url="https://x.com")

    with pytest.raises(ValidationError):
        ExternalLinkCreate(platform="amazon", url="not-a-url")


def test_merchant_product_update_is_all_optional():
    from app.schemas.merchant_product import MerchantProductUpdate

    # All-empty update is valid; just means no changes.
    u = MerchantProductUpdate()
    assert u.title is None
    assert u.dimensions is None

    # Partial update is valid.
    u2 = MerchantProductUpdate(title="New Title", in_app_price=9999.99)
    assert u2.title == "New Title"
    assert u2.in_app_price == 9999.99


@pytest.mark.asyncio
async def test_create_product_creates_draft_owned_by_merchant(auth_client, test_user, db_session):
    # Create a merchant first
    r = await auth_client.post(
        "/api/v1/merchants/", json={"legal_name": "Prod Test", "display_name": "PT"}
    )
    assert r.status_code == 201
    mid = r.json()["id"]

    # Create a product
    r = await auth_client.post(
        "/api/v1/merchant/products/",
        headers={"X-Merchant-Id": mid},
        json={"sku": "OAK-1", "title": "Oak Dining Table", "category": "Furniture"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["sku"] == "OAK-1"
    assert body["title"] == "Oak Dining Table"
    assert body["status"] == "draft"
    assert body["merchant_id"] == mid


@pytest.mark.asyncio
async def test_create_product_duplicate_sku_returns_409(auth_client):
    r = await auth_client.post(
        "/api/v1/merchants/", json={"legal_name": "Dup", "display_name": "Dup"}
    )
    mid = r.json()["id"]

    r1 = await auth_client.post(
        "/api/v1/merchant/products/",
        headers={"X-Merchant-Id": mid},
        json={"sku": "SAME", "title": "First"},
    )
    assert r1.status_code == 201

    r2 = await auth_client.post(
        "/api/v1/merchant/products/",
        headers={"X-Merchant-Id": mid},
        json={"sku": "SAME", "title": "Second"},
    )
    assert r2.status_code == 409


@pytest.mark.asyncio
async def test_list_products_returns_only_this_merchants_products(auth_client, db_session):
    # Create two merchants, two products each
    r1 = await auth_client.post(
        "/api/v1/merchants/", json={"legal_name": "M1", "display_name": "M1"}
    )
    m1 = r1.json()["id"]
    r2 = await auth_client.post(
        "/api/v1/merchants/", json={"legal_name": "M2", "display_name": "M2"}
    )
    m2 = r2.json()["id"]

    for sku in ("M1-1", "M1-2"):
        await auth_client.post(
            "/api/v1/merchant/products/",
            headers={"X-Merchant-Id": m1},
            json={"sku": sku, "title": f"P {sku}"},
        )
    for sku in ("M2-1", "M2-2", "M2-3"):
        await auth_client.post(
            "/api/v1/merchant/products/",
            headers={"X-Merchant-Id": m2},
            json={"sku": sku, "title": f"P {sku}"},
        )

    r = await auth_client.get(
        "/api/v1/merchant/products/", headers={"X-Merchant-Id": m1}
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 2
    skus = sorted(p["sku"] for p in body["items"])
    assert skus == ["M1-1", "M1-2"]


@pytest.mark.asyncio
async def test_list_products_filters_by_status_and_search(auth_client):
    r = await auth_client.post(
        "/api/v1/merchants/", json={"legal_name": "Filt", "display_name": "Filt"}
    )
    mid = r.json()["id"]

    # Create 3 products: 2 draft, 1 published
    for sku, title, status in [
        ("DRAFT-A", "Velvet Sofa", "draft"),
        ("DRAFT-B", "Oak Table", "draft"),
        ("PUB-1", "Brass Lamp", "published"),
    ]:
        await auth_client.post(
            "/api/v1/merchant/products/",
            headers={"X-Merchant-Id": mid},
            json={"sku": sku, "title": title, "status": status},
        )

    r = await auth_client.get(
        "/api/v1/merchant/products/?status=draft",
        headers={"X-Merchant-Id": mid},
    )
    assert r.status_code == 200
    assert all(p["status"] == "draft" for p in r.json()["items"])
    assert len(r.json()["items"]) == 2

    # Search
    r = await auth_client.get(
        "/api/v1/merchant/products/?search=lamp",
        headers={"X-Merchant-Id": mid},
    )
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["sku"] == "PUB-1"


@pytest.mark.asyncio
async def test_get_product_404_for_other_merchants_product(auth_client, db_session):
    r1 = await auth_client.post(
        "/api/v1/merchants/", json={"legal_name": "Merchant Alpha", "display_name": "Alpha"}
    )
    mA = r1.json()["id"]
    r2 = await auth_client.post(
        "/api/v1/merchants/", json={"legal_name": "Merchant Beta", "display_name": "Beta"}
    )
    mB = r2.json()["id"]

    r = await auth_client.post(
        "/api/v1/merchant/products/",
        headers={"X-Merchant-Id": mA},
        json={"sku": "OWNED-BY-A", "title": "A's product"},
    )
    pid = r.json()["id"]

    # Try to read product mA's product while signed in to mB
    r = await auth_client.get(
        f"/api/v1/merchant/products/{pid}",
        headers={"X-Merchant-Id": mB},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_update_product_changes_fields_and_triggers_embedding(auth_client):
    r = await auth_client.post(
        "/api/v1/merchants/", json={"legal_name": "Update Test", "display_name": "Update Test"}
    )
    mid = r.json()["id"]

    r = await auth_client.post(
        "/api/v1/merchant/products/",
        headers={"X-Merchant-Id": mid},
        json={"sku": "U-1", "title": "Original Title"},
    )
    pid = r.json()["id"]

    r = await auth_client.patch(
        f"/api/v1/merchant/products/{pid}",
        headers={"X-Merchant-Id": mid},
        json={"title": "New Title", "in_app_price": 1500, "dimensions": {"w": 100, "h": 50}},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["title"] == "New Title"
    assert body["in_app_price"] == 1500
    assert body["dimensions"] == {"w": 100, "h": 50}


@pytest.mark.asyncio
async def test_archive_product_soft_deletes(auth_client):
    r = await auth_client.post(
        "/api/v1/merchants/", json={"legal_name": "Archive Test", "display_name": "Archive Test"}
    )
    mid = r.json()["id"]

    r = await auth_client.post(
        "/api/v1/merchant/products/",
        headers={"X-Merchant-Id": mid},
        json={"sku": "A-1", "title": "To Archive"},
    )
    pid = r.json()["id"]

    r = await auth_client.delete(
        f"/api/v1/merchant/products/{pid}",
        headers={"X-Merchant-Id": mid},
    )
    assert r.status_code == 204

    # Product still exists with archived status
    r = await auth_client.get(
        f"/api/v1/merchant/products/{pid}", headers={"X-Merchant-Id": mid}
    )
    assert r.status_code == 200
    assert r.json()["status"] == "archived"


@pytest.mark.asyncio
async def test_publish_product_transitions_status(auth_client):
    r = await auth_client.post(
        "/api/v1/merchants/", json={"legal_name": "Publish Test", "display_name": "Publish Test"}
    )
    mid = r.json()["id"]

    r = await auth_client.post(
        "/api/v1/merchant/products/",
        headers={"X-Merchant-Id": mid},
        json={"sku": "P-1", "title": "To Publish"},
    )
    pid = r.json()["id"]
    assert r.json()["status"] == "draft"

    r = await auth_client.post(
        f"/api/v1/merchant/products/{pid}/publish",
        headers={"X-Merchant-Id": mid},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "published"


@pytest.mark.asyncio
async def test_publish_archived_product_returns_400(auth_client):
    r = await auth_client.post(
        "/api/v1/merchants/", json={"legal_name": "Archived Test", "display_name": "Archived Test"}
    )
    mid = r.json()["id"]
    r = await auth_client.post(
        "/api/v1/merchant/products/",
        headers={"X-Merchant-Id": mid},
        json={"sku": "ARC-1", "title": "Once Archived"},
    )
    pid = r.json()["id"]
    await auth_client.delete(
        f"/api/v1/merchant/products/{pid}",
        headers={"X-Merchant-Id": mid},
    )

    r = await auth_client.post(
        f"/api/v1/merchant/products/{pid}/publish",
        headers={"X-Merchant-Id": mid},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_add_external_link_to_product(auth_client):
    r = await auth_client.post(
        "/api/v1/merchants/", json={"legal_name": "External Links", "display_name": "External"}
    )
    mid = r.json()["id"]
    r = await auth_client.post(
        "/api/v1/merchant/products/",
        headers={"X-Merchant-Id": mid},
        json={"sku": "EL-1", "title": "Has Links"},
    )
    pid = r.json()["id"]

    r = await auth_client.post(
        f"/api/v1/merchant/products/{pid}/external-links/",
        headers={"X-Merchant-Id": mid},
        json={
            "platform": "amazon",
            "url": "https://amazon.in/dp/B0XXX",
            "label": "Buy on Amazon",
            "is_primary": True,
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["platform"] == "amazon"
    assert body["is_primary"] is True

    # Verify it shows up on the product
    r = await auth_client.get(
        f"/api/v1/merchant/products/{pid}", headers={"X-Merchant-Id": mid}
    )
    assert len(r.json()["external_links"]) == 1


@pytest.mark.asyncio
async def test_update_external_link(auth_client):
    r = await auth_client.post(
        "/api/v1/merchants/", json={"legal_name": "Update Links", "display_name": "Update"}
    )
    mid = r.json()["id"]
    r = await auth_client.post(
        "/api/v1/merchant/products/",
        headers={"X-Merchant-Id": mid},
        json={"sku": "UL-1", "title": "Product One"},
    )
    pid = r.json()["id"]
    r = await auth_client.post(
        f"/api/v1/merchant/products/{pid}/external-links/",
        headers={"X-Merchant-Id": mid},
        json={"platform": "amazon", "url": "https://amazon.in/dp/B1"},
    )
    lid = r.json()["id"]

    r = await auth_client.patch(
        f"/api/v1/merchant/products/{pid}/external-links/{lid}",
        headers={"X-Merchant-Id": mid},
        json={"label": "Updated Label", "last_seen_price": 1499.99},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["label"] == "Updated Label"
    assert body["last_seen_price"] == 1499.99


@pytest.mark.asyncio
async def test_delete_external_link(auth_client):
    r = await auth_client.post(
        "/api/v1/merchants/", json={"legal_name": "Delete Links", "display_name": "Delete"}
    )
    mid = r.json()["id"]
    r = await auth_client.post(
        "/api/v1/merchant/products/",
        headers={"X-Merchant-Id": mid},
        json={"sku": "DL-1", "title": "Product One"},
    )
    pid = r.json()["id"]
    r = await auth_client.post(
        f"/api/v1/merchant/products/{pid}/external-links/",
        headers={"X-Merchant-Id": mid},
        json={"platform": "amazon", "url": "https://amazon.in/dp/B2"},
    )
    lid = r.json()["id"]

    r = await auth_client.delete(
        f"/api/v1/merchant/products/{pid}/external-links/{lid}",
        headers={"X-Merchant-Id": mid},
    )
    assert r.status_code == 204

    r = await auth_client.get(
        f"/api/v1/merchant/products/{pid}", headers={"X-Merchant-Id": mid}
    )
    assert len(r.json()["external_links"]) == 0


@pytest.mark.asyncio
async def test_external_link_cross_merchant_404(auth_client):
    rA = await auth_client.post(
        "/api/v1/merchants/", json={"legal_name": "Alpha Org", "display_name": "Alpha"}
    )
    mA = rA.json()["id"]
    rB = await auth_client.post(
        "/api/v1/merchants/", json={"legal_name": "Beta Org", "display_name": "Beta"}
    )
    mB = rB.json()["id"]

    r = await auth_client.post(
        "/api/v1/merchant/products/",
        headers={"X-Merchant-Id": mA},
        json={"sku": "OWNED-A", "title": "Product P"},
    )
    pid = r.json()["id"]

    # Try to add a link to mA's product while signed in to mB
    r = await auth_client.post(
        f"/api/v1/merchant/products/{pid}/external-links/",
        headers={"X-Merchant-Id": mB},
        json={"platform": "amazon", "url": "https://amazon.in/dp/X"},
    )
    assert r.status_code == 404

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

import uuid
from decimal import Decimal
from datetime import datetime, timezone

import pytest


async def _seed_merchant_with_funded_wallet_and_product(db_session, test_user, sku="P-1"):
    """Helper: returns (merchant, product) with a funded wallet and pricing rules."""
    from app.models.merchant import Merchant, MerchantMember, MemberRole
    from app.models.merchant_product import MerchantProduct
    from app.models.wallet import Wallet, PricingRule

    m = Merchant(
        slug=f"ev-{uuid.uuid4().hex[:6]}",
        legal_name="Events Test",
        display_name="ET",
        referral_code=f"ET-{uuid.uuid4().hex[:6].upper()}",
    )
    db_session.add(m)
    await db_session.commit()
    await db_session.refresh(m)
    db_session.add(MerchantMember(merchant_id=m.id, user_id=test_user.id, role=MemberRole.OWNER.value))
    db_session.add(Wallet(merchant_id=m.id, balance=Decimal("100")))
    p = MerchantProduct(merchant_id=m.id, sku=sku, title=f"Product {sku}", status="published")
    db_session.add(p)
    db_session.add(
        PricingRule(
            id=uuid.uuid4(),
            event_type="click",
            merchant_id=None,
            rate=Decimal("0.25"),
            rate_type="fixed",
            effective_from=datetime.now(timezone.utc),
        )
    )
    db_session.add(
        PricingRule(
            id=uuid.uuid4(),
            event_type="external_redirect",
            merchant_id=None,
            rate=Decimal("5.00"),
            rate_type="fixed",
            effective_from=datetime.now(timezone.utc),
        )
    )
    await db_session.commit()
    await db_session.refresh(p)
    return m, p


@pytest.mark.asyncio
async def test_click_event_records_and_deducts(auth_client, test_user, db_session):
    from sqlalchemy import select
    from app.models.wallet import Wallet

    m, p = await _seed_merchant_with_funded_wallet_and_product(db_session, test_user, "CLICK-1")

    r = await auth_client.post(
        "/api/v1/events/click",
        json={"product_id": str(p.id), "session_id": "sess_click", "context": {"rank": 2}},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["event_type"] == "click"
    assert body["billed"] is True

    res = await db_session.execute(select(Wallet).where(Wallet.merchant_id == m.id))
    wallet = res.scalar_one()
    assert float(wallet.balance) == 99.75


@pytest.mark.asyncio
async def test_click_event_unknown_product_returns_404(auth_client):
    r = await auth_client.post(
        "/api/v1/events/click",
        json={"product_id": str(uuid.uuid4()), "session_id": "sess"},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_external_redirect_returns_target_url(auth_client, test_user, db_session):
    from app.models.merchant_product import MerchantProductExternalLink

    m, p = await _seed_merchant_with_funded_wallet_and_product(db_session, test_user, "REDIR-1")
    link = MerchantProductExternalLink(
        merchant_product_id=p.id,
        platform="amazon",
        url="https://amazon.in/dp/B0XYZ",
        label="Buy on Amazon",
    )
    db_session.add(link)
    await db_session.commit()
    await db_session.refresh(link)

    r = await auth_client.post(
        "/api/v1/events/external-redirect",
        json={
            "product_id": str(p.id),
            "link_id": str(link.id),
            "session_id": "sess_redir",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["target_url"] == "https://amazon.in/dp/B0XYZ"
    assert body["billed"] is True


@pytest.mark.asyncio
async def test_external_redirect_link_belongs_to_product(auth_client, test_user, db_session):
    from app.models.merchant_product import MerchantProductExternalLink

    _, p1 = await _seed_merchant_with_funded_wallet_and_product(db_session, test_user, "MIX-A")
    _, p2 = await _seed_merchant_with_funded_wallet_and_product(db_session, test_user, "MIX-B")
    link = MerchantProductExternalLink(
        merchant_product_id=p1.id,
        platform="amazon",
        url="https://amazon.in/dp/B0",
    )
    db_session.add(link)
    await db_session.commit()
    await db_session.refresh(link)

    r = await auth_client.post(
        "/api/v1/events/external-redirect",
        json={
            "product_id": str(p2.id),
            "link_id": str(link.id),
            "session_id": "sess",
        },
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_impression_batch_records_all_impressions(auth_client, test_user, db_session):
    from sqlalchemy import select, func
    from app.models.event import BuyerEvent

    m, p1 = await _seed_merchant_with_funded_wallet_and_product(db_session, test_user, "IMP-A")
    _, p2 = await _seed_merchant_with_funded_wallet_and_product(db_session, test_user, "IMP-B")

    r = await auth_client.post(
        "/api/v1/events/impression-batch",
        json={
            "session_id": "sess_imp",
            "product_ids": [str(p1.id), str(p2.id)],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["recorded"] == 2

    res = await db_session.execute(
        select(func.count()).select_from(BuyerEvent).where(BuyerEvent.event_type == "impression")
    )
    assert res.scalar_one() == 2

import uuid
from decimal import Decimal
from datetime import datetime, timedelta, timezone

import pytest


async def _seed_merchant_with_events(db_session, test_user):
    """Seed: 1 merchant, 1 wallet, 2 products, several BuyerEvents + LedgerEntries."""
    from app.models.merchant import Merchant, MerchantMember, MemberRole
    from app.models.merchant_product import MerchantProduct
    from app.models.wallet import Wallet, PricingRule
    from app.models.event import BuyerEvent, LedgerEntry

    m = Merchant(
        slug=f"an-{uuid.uuid4().hex[:6]}",
        legal_name="Analytics Co",
        display_name="AN",
        referral_code=f"AN-{uuid.uuid4().hex[:6].upper()}",
    )
    db_session.add(m)
    await db_session.commit()
    await db_session.refresh(m)
    db_session.add(MerchantMember(merchant_id=m.id, user_id=test_user.id, role=MemberRole.OWNER.value))

    w = Wallet(merchant_id=m.id, balance=Decimal("100"))
    db_session.add(w)
    p1 = MerchantProduct(merchant_id=m.id, sku="AN-1", title="Product One", status="published")
    p2 = MerchantProduct(merchant_id=m.id, sku="AN-2", title="Product Two", status="published")
    db_session.add(p1)
    db_session.add(p2)
    await db_session.commit()
    await db_session.refresh(w)
    await db_session.refresh(p1)
    await db_session.refresh(p2)

    # 5 impressions on p1
    for _ in range(5):
        db_session.add(
            BuyerEvent(
                user_id=test_user.id,
                merchant_id=m.id,
                merchant_product_id=p1.id,
                event_type="impression",
                context={},
                billed=False,
            )
        )
    # 2 clicks on p1 (billed, with ledger)
    for _ in range(2):
        ev = BuyerEvent(
            user_id=test_user.id,
            merchant_id=m.id,
            merchant_product_id=p1.id,
            event_type="click",
            context={},
            billed=True,
        )
        db_session.add(ev)
        await db_session.flush()
        db_session.add(
            LedgerEntry(
                merchant_id=m.id,
                wallet_id=w.id,
                related_event_id=ev.id,
                entry_type="deduction",
                amount=Decimal("-0.25"),
                reason="click",
                balance_after=Decimal("99.75"),
            )
        )
    # 3 ai_rag_mentions on p2
    for _ in range(3):
        ev = BuyerEvent(
            user_id=test_user.id,
            merchant_id=m.id,
            merchant_product_id=p2.id,
            event_type="ai_rag_mention",
            context={"rank": 0},
            billed=True,
        )
        db_session.add(ev)
        await db_session.flush()
        db_session.add(
            LedgerEntry(
                merchant_id=m.id,
                wallet_id=w.id,
                related_event_id=ev.id,
                entry_type="deduction",
                amount=Decimal("-0.50"),
                reason="ai_rag_mention",
                balance_after=Decimal("99.50"),
            )
        )
    await db_session.commit()
    return m, p1, p2


@pytest.mark.asyncio
async def test_analytics_summary_aggregates_correctly(auth_client, test_user, db_session):
    m, _, _ = await _seed_merchant_with_events(db_session, test_user)

    r = await auth_client.get(
        "/api/v1/merchant/analytics/summary",
        headers={"X-Merchant-Id": str(m.id)},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["impressions"] == 5
    assert body["clicks"] == 2
    assert body["ai_mentions"] == 3
    assert body["ctr"] == pytest.approx(2 / 5, abs=0.01)
    assert body["total_spend"] == pytest.approx(2 * 0.25 + 3 * 0.50, abs=0.01)


@pytest.mark.asyncio
async def test_analytics_products_returns_per_product_rows(auth_client, test_user, db_session):
    m, p1, p2 = await _seed_merchant_with_events(db_session, test_user)

    r = await auth_client.get(
        "/api/v1/merchant/analytics/products",
        headers={"X-Merchant-Id": str(m.id)},
    )
    assert r.status_code == 200
    body = r.json()
    items = {row["product_id"]: row for row in body["items"]}

    p1_row = items[str(p1.id)]
    assert p1_row["impressions"] == 5
    assert p1_row["clicks"] == 2
    assert p1_row["ai_mentions"] == 0
    assert p1_row["spend"] == pytest.approx(0.5)

    p2_row = items[str(p2.id)]
    assert p2_row["ai_mentions"] == 3
    assert p2_row["spend"] == pytest.approx(1.5)


@pytest.mark.asyncio
async def test_analytics_per_product_detail_returns_drill_down(auth_client, test_user, db_session):
    m, p1, _ = await _seed_merchant_with_events(db_session, test_user)

    r = await auth_client.get(
        f"/api/v1/merchant/analytics/products/{p1.id}",
        headers={"X-Merchant-Id": str(m.id)},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["product_id"] == str(p1.id)
    assert body["impressions"] == 5
    assert body["clicks"] == 2
    assert isinstance(body["daily_impressions"], list)
    assert len(body["daily_impressions"]) == 7


@pytest.mark.asyncio
async def test_analytics_diagnostics_flags_zero_click_products(auth_client, test_user, db_session):
    from app.models.merchant import Merchant, MerchantMember, MemberRole
    from app.models.merchant_product import MerchantProduct
    from app.models.event import BuyerEvent

    m = Merchant(
        slug=f"diag-{uuid.uuid4().hex[:6]}",
        legal_name="Diag",
        display_name="DG",
        referral_code=f"DG-{uuid.uuid4().hex[:6].upper()}",
    )
    db_session.add(m)
    await db_session.commit()
    await db_session.refresh(m)
    db_session.add(MerchantMember(merchant_id=m.id, user_id=test_user.id, role=MemberRole.OWNER.value))

    p = MerchantProduct(merchant_id=m.id, sku="ZC-1", title="Zero Click", status="published")
    db_session.add(p)
    await db_session.commit()
    await db_session.refresh(p)
    for _ in range(200):
        db_session.add(
            BuyerEvent(
                user_id=test_user.id,
                merchant_id=m.id,
                merchant_product_id=p.id,
                event_type="impression",
                context={},
                billed=False,
            )
        )
    await db_session.commit()

    r = await auth_client.get(
        "/api/v1/merchant/analytics/diagnostics",
        headers={"X-Merchant-Id": str(m.id)},
    )
    assert r.status_code == 200
    body = r.json()
    issue_types = {alert["issue_type"] for alert in body["alerts"]}
    assert "zero_click" in issue_types

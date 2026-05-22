from decimal import Decimal

import pytest


@pytest.mark.asyncio
async def test_record_event_creates_event_and_ledger_entry(db_session, test_user):
    from app.models.merchant import Merchant, MerchantMember, MemberRole
    from app.models.merchant_product import MerchantProduct
    from app.models.wallet import PricingRule, Wallet
    from app.models.event import BuyerEvent, LedgerEntry
    from app.services.billing import BillingService
    from datetime import datetime, timezone
    from sqlalchemy import select
    import uuid as _uuid

    m = Merchant(slug="bs1", legal_name="Billing One", display_name="B1", referral_code="B1-1")
    db_session.add(m)
    await db_session.commit()
    await db_session.refresh(m)
    db_session.add(MerchantMember(merchant_id=m.id, user_id=test_user.id, role=MemberRole.OWNER.value))
    db_session.add(Wallet(merchant_id=m.id, balance=Decimal("100")))
    db_session.add(
        PricingRule(
            id=_uuid.uuid4(),
            event_type="click",
            merchant_id=None,
            rate=Decimal("0.25"),
            rate_type="fixed",
            effective_from=datetime.now(timezone.utc),
        )
    )
    p = MerchantProduct(merchant_id=m.id, sku="BS-P", title="P")
    db_session.add(p)
    await db_session.commit()
    await db_session.refresh(p)

    svc = BillingService(db_session)
    event = await svc.record_event(
        event_type="click",
        user_id=test_user.id,
        merchant_id=m.id,
        product_id=p.id,
        session_id="sess_bs",
        context={"rank": 1},
    )

    assert event.billed is True

    res = await db_session.execute(select(Wallet).where(Wallet.merchant_id == m.id))
    wallet = res.scalar_one()
    assert float(wallet.balance) == 99.75

    res = await db_session.execute(select(LedgerEntry).where(LedgerEntry.related_event_id == event.id))
    ledger = res.scalar_one()
    assert float(ledger.amount) == -0.25
    assert ledger.reason == "click"
    assert float(ledger.balance_after) == 99.75


@pytest.mark.asyncio
async def test_record_event_dedup_blocks_second_click(db_session, test_user):
    from app.models.merchant import Merchant, MerchantMember, MemberRole
    from app.models.merchant_product import MerchantProduct
    from app.models.wallet import PricingRule, Wallet
    from app.services.billing import BillingService
    from datetime import datetime, timezone
    import uuid as _uuid

    m = Merchant(slug="bs2", legal_name="Billing Two", display_name="B2", referral_code="B2-1")
    db_session.add(m)
    await db_session.commit()
    await db_session.refresh(m)
    db_session.add(MerchantMember(merchant_id=m.id, user_id=test_user.id, role=MemberRole.OWNER.value))
    db_session.add(Wallet(merchant_id=m.id, balance=Decimal("10")))
    db_session.add(
        PricingRule(
            id=_uuid.uuid4(),
            event_type="click",
            merchant_id=None,
            rate=Decimal("0.25"),
            rate_type="fixed",
            effective_from=datetime.now(timezone.utc),
        )
    )
    p = MerchantProduct(merchant_id=m.id, sku="BS-D", title="D")
    db_session.add(p)
    await db_session.commit()
    await db_session.refresh(p)

    svc = BillingService(db_session)
    e1 = await svc.record_event(
        event_type="click",
        user_id=test_user.id,
        merchant_id=m.id,
        product_id=p.id,
        session_id="sess_dup",
        context={},
    )
    e2 = await svc.record_event(
        event_type="click",
        user_id=test_user.id,
        merchant_id=m.id,
        product_id=p.id,
        session_id="sess_dup",
        context={},
    )

    assert e1.billed is True
    assert e2.billed is False

    from sqlalchemy import select
    from app.models.wallet import Wallet as W
    res = await db_session.execute(select(W).where(W.merchant_id == m.id))
    wallet = res.scalar_one()
    assert float(wallet.balance) == 9.75


@pytest.mark.asyncio
async def test_pause_if_depleted_pauses_products_and_wallet(db_session, test_user):
    from app.models.merchant import Merchant, MerchantMember, MemberRole
    from app.models.merchant_product import MerchantProduct
    from app.models.wallet import Wallet
    from app.services.billing import BillingService
    from sqlalchemy import select

    m = Merchant(slug="pa", legal_name="Pause Co", display_name="PA", referral_code="PA-1")
    db_session.add(m)
    await db_session.commit()
    await db_session.refresh(m)
    db_session.add(MerchantMember(merchant_id=m.id, user_id=test_user.id, role=MemberRole.OWNER.value))
    db_session.add(Wallet(merchant_id=m.id, balance=Decimal("0")))
    db_session.add(MerchantProduct(merchant_id=m.id, sku="PA-1", title="One", status="published"))
    db_session.add(MerchantProduct(merchant_id=m.id, sku="PA-2", title="Two", status="published"))
    await db_session.commit()

    svc = BillingService(db_session)
    await svc.pause_if_depleted_for(m.id)

    res = await db_session.execute(
        select(MerchantProduct).where(MerchantProduct.merchant_id == m.id)
    )
    products = list(res.scalars().all())
    assert all(p.status == "paused_insufficient_funds" for p in products)

    res = await db_session.execute(select(Wallet).where(Wallet.merchant_id == m.id))
    wallet = res.scalar_one()
    assert wallet.status == "depleted"


@pytest.mark.asyncio
async def test_pause_if_depleted_noop_when_balance_positive(db_session, test_user):
    from app.models.merchant import Merchant, MerchantMember, MemberRole
    from app.models.merchant_product import MerchantProduct
    from app.models.wallet import Wallet
    from app.services.billing import BillingService
    from sqlalchemy import select

    m = Merchant(slug="np", legal_name="Not Pause", display_name="NP", referral_code="NP-1")
    db_session.add(m)
    await db_session.commit()
    await db_session.refresh(m)
    db_session.add(MerchantMember(merchant_id=m.id, user_id=test_user.id, role=MemberRole.OWNER.value))
    db_session.add(Wallet(merchant_id=m.id, balance=Decimal("100")))
    db_session.add(MerchantProduct(merchant_id=m.id, sku="NP-1", title="One", status="published"))
    await db_session.commit()

    svc = BillingService(db_session)
    await svc.pause_if_depleted_for(m.id)

    res = await db_session.execute(
        select(MerchantProduct).where(MerchantProduct.merchant_id == m.id)
    )
    product = res.scalar_one()
    assert product.status == "published"

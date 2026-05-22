import uuid
from decimal import Decimal
from datetime import datetime, timezone

import pytest


@pytest.mark.asyncio
async def test_create_buyer_event_persists_required_fields(db_session, test_user):
    from app.models.merchant import Merchant, MerchantMember, MemberRole
    from app.models.merchant_product import MerchantProduct
    from app.models.event import BuyerEvent

    m = Merchant(slug="ev1", legal_name="Event Co", display_name="EV", referral_code="EV-1")
    db_session.add(m)
    await db_session.commit()
    await db_session.refresh(m)
    db_session.add(MerchantMember(merchant_id=m.id, user_id=test_user.id, role=MemberRole.OWNER.value))
    p = MerchantProduct(merchant_id=m.id, sku="EV-P", title="Product P")
    db_session.add(p)
    await db_session.commit()
    await db_session.refresh(p)

    ev = BuyerEvent(
        user_id=test_user.id,
        merchant_id=m.id,
        merchant_product_id=p.id,
        event_type="click",
        context={"rank": 1, "source": "search"},
        user_session_id="sess_abc",
    )
    db_session.add(ev)
    await db_session.commit()
    await db_session.refresh(ev)

    assert isinstance(ev.id, uuid.UUID)
    assert ev.billed is False
    assert ev.context == {"rank": 1, "source": "search"}


@pytest.mark.asyncio
async def test_ledger_entry_with_signed_amount(db_session, test_user):
    from app.models.merchant import Merchant, MerchantMember, MemberRole
    from app.models.wallet import Wallet
    from app.models.event import LedgerEntry

    m = Merchant(slug="le1", legal_name="Ledger Co", display_name="LE", referral_code="LE-1")
    db_session.add(m)
    await db_session.commit()
    await db_session.refresh(m)
    db_session.add(MerchantMember(merchant_id=m.id, user_id=test_user.id, role=MemberRole.OWNER.value))
    w = Wallet(merchant_id=m.id, balance=Decimal("1000"))
    db_session.add(w)
    await db_session.commit()
    await db_session.refresh(w)

    deduction = LedgerEntry(
        merchant_id=m.id,
        wallet_id=w.id,
        entry_type="deduction",
        amount=Decimal("-0.25"),
        reason="click",
        balance_after=Decimal("999.75"),
    )
    db_session.add(deduction)
    await db_session.commit()
    await db_session.refresh(deduction)

    assert isinstance(deduction.id, uuid.UUID)
    assert float(deduction.amount) == -0.25
    assert float(deduction.balance_after) == 999.75


@pytest.mark.asyncio
async def test_buyer_event_dedup_unique_constraint(db_session, test_user):
    from app.models.merchant import Merchant
    from app.models.merchant_product import MerchantProduct
    from app.models.event import BuyerEventDedup

    m = Merchant(slug="dd", legal_name="Dedup Co", display_name="DD", referral_code="DD-1")
    db_session.add(m)
    await db_session.commit()
    await db_session.refresh(m)
    p = MerchantProduct(merchant_id=m.id, sku="DD-P", title="P")
    db_session.add(p)
    await db_session.commit()
    await db_session.refresh(p)

    d1 = BuyerEventDedup(
        event_type="click",
        user_session_id="sess_xyz",
        merchant_product_id=p.id,
        hour_bucket=123456,
    )
    db_session.add(d1)
    await db_session.commit()

    d2 = BuyerEventDedup(
        event_type="click",
        user_session_id="sess_xyz",
        merchant_product_id=p.id,
        hour_bucket=123456,
    )
    db_session.add(d2)
    with pytest.raises(Exception):
        await db_session.commit()
    await db_session.rollback()

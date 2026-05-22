import uuid
from decimal import Decimal

import pytest


@pytest.mark.asyncio
async def test_wallet_one_per_merchant_uniqueness(db_session, test_user):
    from app.models.merchant import Merchant, MerchantMember, MemberRole
    from app.models.wallet import Wallet

    m = Merchant(slug="w1", legal_name="Wallet One", display_name="W1", referral_code="W1-1")
    db_session.add(m)
    await db_session.commit()
    await db_session.refresh(m)
    db_session.add(MerchantMember(merchant_id=m.id, user_id=test_user.id, role=MemberRole.OWNER.value))
    await db_session.commit()

    w1 = Wallet(merchant_id=m.id)
    db_session.add(w1)
    await db_session.commit()
    await db_session.refresh(w1)
    assert w1.currency == "INR"
    assert Decimal(str(w1.balance)) == Decimal("0")
    assert w1.status == "active"

    w2 = Wallet(merchant_id=m.id)
    db_session.add(w2)
    with pytest.raises(Exception):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_transaction_gateway_ref_unique_when_set(db_session, test_user):
    from app.models.merchant import Merchant, MerchantMember, MemberRole
    from app.models.wallet import Transaction

    m = Merchant(slug="t1", legal_name="Txn One", display_name="T1", referral_code="T1-1")
    db_session.add(m)
    await db_session.commit()
    await db_session.refresh(m)
    db_session.add(MerchantMember(merchant_id=m.id, user_id=test_user.id, role=MemberRole.OWNER.value))
    await db_session.commit()

    t1 = Transaction(
        merchant_id=m.id,
        amount=Decimal("1000"),
        status="successful",
        gateway_ref="pay_ABCDEF",
    )
    db_session.add(t1)
    await db_session.commit()

    t2 = Transaction(
        merchant_id=m.id,
        amount=Decimal("2000"),
        status="successful",
        gateway_ref="pay_ABCDEF",
    )
    db_session.add(t2)
    with pytest.raises(Exception):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_transaction_allows_multiple_pending_without_gateway_ref(db_session, test_user):
    from app.models.merchant import Merchant, MerchantMember, MemberRole
    from app.models.wallet import Transaction

    m = Merchant(slug="t2", legal_name="Txn Two", display_name="T2", referral_code="T2-1")
    db_session.add(m)
    await db_session.commit()
    await db_session.refresh(m)
    db_session.add(MerchantMember(merchant_id=m.id, user_id=test_user.id, role=MemberRole.OWNER.value))
    await db_session.commit()

    for i in range(3):
        db_session.add(
            Transaction(
                merchant_id=m.id,
                amount=Decimal("500"),
                status="pending",
                razorpay_order_id=f"order_{i}",
            )
        )
    await db_session.commit()  # should NOT raise


def test_pricing_rule_enums():
    from app.models.wallet import PricingRule, RateType

    assert RateType.FIXED.value == "fixed"
    assert RateType.PERCENTAGE.value == "percentage"
    pr = PricingRule(event_type="click", rate=Decimal("0.25"), rate_type="fixed")
    assert pr.rate_type == "fixed"


def test_topup_intent_request_validates_amount():
    from app.schemas.wallet import TopupIntentRequest
    from pydantic import ValidationError

    valid = TopupIntentRequest(amount=1000)
    assert valid.currency == "INR"

    with pytest.raises(ValidationError):
        TopupIntentRequest(amount=0)
    with pytest.raises(ValidationError):
        TopupIntentRequest(amount=-50)
    with pytest.raises(ValidationError):
        TopupIntentRequest(amount=10_000_000)


def test_topup_confirm_request_requires_all_fields():
    from app.schemas.wallet import TopupConfirmRequest
    from pydantic import ValidationError

    valid = TopupConfirmRequest(
        order_id="order_AB",
        payment_id="pay_XY",
        signature="abc123",
    )
    assert valid.order_id == "order_AB"

    with pytest.raises(ValidationError):
        TopupConfirmRequest(order_id="x", payment_id="y")


def test_wallet_settings_update_threshold_only():
    from app.schemas.wallet import WalletSettingsUpdate
    from pydantic import ValidationError

    valid = WalletSettingsUpdate(low_balance_threshold=2500)
    assert valid.low_balance_threshold == 2500

    empty = WalletSettingsUpdate()
    assert empty.low_balance_threshold is None

    with pytest.raises(ValidationError):
        WalletSettingsUpdate(low_balance_threshold=-100)

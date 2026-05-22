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


@pytest.mark.asyncio
async def test_create_merchant_also_creates_wallet(auth_client, db_session):
    import uuid as _uuid
    from sqlalchemy import select
    from app.models.wallet import Wallet

    r = await auth_client.post(
        "/api/v1/merchants/",
        json={"legal_name": "Auto Wallet Co", "display_name": "Auto Wallet"},
    )
    assert r.status_code == 201
    mid = _uuid.UUID(r.json()["id"])

    res = await db_session.execute(
        select(Wallet).where(Wallet.merchant_id == mid)
    )
    wallet = res.scalar_one_or_none()
    assert wallet is not None
    assert wallet.currency == "INR"
    assert float(wallet.balance) == 0.0
    assert wallet.status == "active"


@pytest.mark.asyncio
async def test_get_wallet_returns_zero_balance_for_new_merchant(auth_client):
    r = await auth_client.post(
        "/api/v1/merchants/", json={"legal_name": "Wallet Read", "display_name": "WR"}
    )
    mid = r.json()["id"]

    r = await auth_client.get(
        "/api/v1/merchant/wallet/", headers={"X-Merchant-Id": mid}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["currency"] == "INR"
    assert body["balance"] == 0.0
    assert body["status"] == "active"
    assert body["low_balance_threshold"] == 500.0


@pytest.mark.asyncio
async def test_list_transactions_returns_paginated_results(auth_client, db_session):
    from decimal import Decimal
    from app.models.wallet import Transaction
    import uuid as uuid_mod

    r = await auth_client.post(
        "/api/v1/merchants/", json={"legal_name": "Txn List", "display_name": "TL"}
    )
    mid = r.json()["id"]

    for i in range(5):
        db_session.add(
            Transaction(
                merchant_id=uuid_mod.UUID(mid),
                amount=Decimal(str(100 * (i + 1))),
                status="successful" if i % 2 == 0 else "failed",
                gateway_ref=f"pay_{i:04d}",
                razorpay_order_id=f"order_{i:04d}",
            )
        )
    await db_session.commit()

    r = await auth_client.get(
        "/api/v1/merchant/wallet/transactions",
        headers={"X-Merchant-Id": mid},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 5
    assert len(body["items"]) == 5


@pytest.mark.asyncio
async def test_patch_wallet_settings_updates_threshold(auth_client):
    r = await auth_client.post(
        "/api/v1/merchants/", json={"legal_name": "Threshold Test", "display_name": "TT"}
    )
    mid = r.json()["id"]

    r = await auth_client.patch(
        "/api/v1/merchant/wallet/settings",
        headers={"X-Merchant-Id": mid},
        json={"low_balance_threshold": 2500},
    )
    assert r.status_code == 200
    assert r.json()["low_balance_threshold"] == 2500.0


@pytest.mark.asyncio
async def test_topup_intent_creates_pending_transaction(auth_client, db_session):
    from unittest.mock import patch
    from sqlalchemy import select
    from app.models.wallet import Transaction

    r = await auth_client.post(
        "/api/v1/merchants/", json={"legal_name": "Intent Test", "display_name": "IT"}
    )
    mid = r.json()["id"]

    fake_order = {"id": "order_TESTABC", "amount": 100000, "currency": "INR"}
    with (
        patch("app.routers.wallet.create_order", return_value=fake_order),
        patch("app.routers.wallet._get_razorpay_key_id", return_value="rzp_test_xxx"),
    ):
        r = await auth_client.post(
            "/api/v1/merchant/wallet/topup/intent",
            headers={"X-Merchant-Id": mid},
            json={"amount": 1000},
        )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["order_id"] == "order_TESTABC"
    assert body["amount"] == 1000
    assert body["razorpay_key_id"] == "rzp_test_xxx"

    res = await db_session.execute(
        select(Transaction).where(Transaction.razorpay_order_id == "order_TESTABC")
    )
    txn = res.scalar_one()
    assert txn.status == "pending"
    assert float(txn.amount) == 1000.0


@pytest.mark.asyncio
async def test_topup_confirm_credits_wallet_when_signature_valid(auth_client, db_session):
    from unittest.mock import patch
    from sqlalchemy import select
    from decimal import Decimal
    import uuid as uuid_mod
    from app.models.wallet import Transaction, Wallet

    r = await auth_client.post(
        "/api/v1/merchants/", json={"legal_name": "Confirm Test", "display_name": "CT"}
    )
    mid = r.json()["id"]

    # Manually seed a pending transaction
    txn = Transaction(
        merchant_id=uuid_mod.UUID(mid),
        amount=Decimal("1500"),
        status="pending",
        razorpay_order_id="order_CONFIRM",
    )
    db_session.add(txn)
    await db_session.commit()

    with patch("app.routers.wallet.verify_payment_signature", return_value=True):
        r = await auth_client.post(
            "/api/v1/merchant/wallet/topup/confirm",
            headers={"X-Merchant-Id": mid},
            json={
                "order_id": "order_CONFIRM",
                "payment_id": "pay_CONFIRM",
                "signature": "irrelevant_in_mock",
            },
        )

    assert r.status_code == 200, r.text

    await db_session.refresh(txn)
    assert txn.status == "successful"
    assert txn.gateway_ref == "pay_CONFIRM"

    res = await db_session.execute(select(Wallet).where(Wallet.merchant_id == uuid_mod.UUID(mid)))
    wallet = res.scalar_one()
    assert float(wallet.balance) == 1500.0


@pytest.mark.asyncio
async def test_topup_confirm_idempotent_returns_existing(auth_client, db_session):
    from unittest.mock import patch
    from sqlalchemy import select
    from decimal import Decimal
    import uuid as uuid_mod
    from app.models.wallet import Transaction, Wallet

    r = await auth_client.post(
        "/api/v1/merchants/", json={"legal_name": "Idem Test", "display_name": "IT2"}
    )
    mid = r.json()["id"]

    # Seed an already-credited transaction
    txn = Transaction(
        merchant_id=uuid_mod.UUID(mid),
        amount=Decimal("500"),
        status="successful",
        razorpay_order_id="order_IDEM",
        gateway_ref="pay_IDEM",
    )
    db_session.add(txn)
    res = await db_session.execute(select(Wallet).where(Wallet.merchant_id == uuid_mod.UUID(mid)))
    wallet = res.scalar_one()
    wallet.balance = Decimal("500")
    await db_session.commit()

    # Call confirm again — should be a no-op (already successful)
    with patch("app.routers.wallet.verify_payment_signature", return_value=True):
        r = await auth_client.post(
            "/api/v1/merchant/wallet/topup/confirm",
            headers={"X-Merchant-Id": mid},
            json={
                "order_id": "order_IDEM",
                "payment_id": "pay_IDEM",
                "signature": "x",
            },
        )

    assert r.status_code == 200
    # Balance NOT double-credited
    await db_session.refresh(wallet)
    assert float(wallet.balance) == 500.0


@pytest.mark.asyncio
async def test_topup_confirm_bad_signature_returns_400(auth_client, db_session):
    from unittest.mock import patch
    from decimal import Decimal
    import uuid as uuid_mod
    from app.models.wallet import Transaction

    r = await auth_client.post(
        "/api/v1/merchants/", json={"legal_name": "Bad Sig", "display_name": "BS"}
    )
    mid = r.json()["id"]

    txn = Transaction(
        merchant_id=uuid_mod.UUID(mid),
        amount=Decimal("100"),
        status="pending",
        razorpay_order_id="order_BAD",
    )
    db_session.add(txn)
    await db_session.commit()

    with patch("app.routers.wallet.verify_payment_signature", return_value=False):
        r = await auth_client.post(
            "/api/v1/merchant/wallet/topup/confirm",
            headers={"X-Merchant-Id": mid},
            json={
                "order_id": "order_BAD",
                "payment_id": "pay_BAD",
                "signature": "tampered",
            },
        )

    assert r.status_code == 400

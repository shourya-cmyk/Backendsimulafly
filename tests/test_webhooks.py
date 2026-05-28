import json
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_webhook_credits_wallet_on_payment_captured(client, db_session):
    """Verify the webhook end-to-end with a valid signature."""
    from sqlalchemy import select
    from app.models.merchant import Merchant, MerchantMember, MemberRole
    from app.models.user import User
    from app.models.wallet import Transaction, Wallet
    from app.core.security import hash_password

    user = User(email=f"hook-{uuid4().hex[:8]}@x.com", hashed_password=hash_password("password123"))
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    merchant = Merchant(slug=f"hook-{uuid4().hex[:6]}", legal_name="Hook M", display_name="HM", referral_code=f"HM-{uuid4().hex[:6].upper()}")
    db_session.add(merchant)
    await db_session.commit()
    await db_session.refresh(merchant)

    db_session.add(MerchantMember(merchant_id=merchant.id, user_id=user.id, role=MemberRole.OWNER.value))
    db_session.add(Wallet(merchant_id=merchant.id))
    db_session.add(
        Transaction(
            merchant_id=merchant.id,
            amount=Decimal("2000"),
            status="pending",
            razorpay_order_id="order_HOOK",
        )
    )
    await db_session.commit()

    payload = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_HOOK",
                    "order_id": "order_HOOK",
                    "amount": 200000,
                    "method": "upi",
                }
            }
        },
    }
    raw_body = json.dumps(payload).encode("utf-8")

    with patch("app.routers.webhooks.verify_webhook_signature", return_value=True):
        r = await client.post(
            "/api/v1/webhooks/razorpay",
            content=raw_body,
            headers={
                "Content-Type": "application/json",
                "X-Razorpay-Signature": "mocked",
            },
        )
    assert r.status_code == 200

    res = await db_session.execute(select(Wallet).where(Wallet.merchant_id == merchant.id))
    wallet = res.scalar_one()
    assert float(wallet.balance) == 2000.0

    res = await db_session.execute(
        select(Transaction).where(Transaction.razorpay_order_id == "order_HOOK")
    )
    txn = res.scalar_one()
    assert txn.status == "successful"
    assert txn.gateway_ref == "pay_HOOK"


@pytest.mark.asyncio
async def test_webhook_rejects_bad_signature(client):
    with patch("app.routers.webhooks.verify_webhook_signature", return_value=False):
        r = await client.post(
            "/api/v1/webhooks/razorpay",
            content=b'{"event":"payment.captured"}',
            headers={
                "Content-Type": "application/json",
                "X-Razorpay-Signature": "tampered",
            },
        )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_webhook_idempotent_when_already_credited(client, db_session):
    from sqlalchemy import select
    from app.models.merchant import Merchant
    from app.models.wallet import Transaction, Wallet
    from uuid import uuid4 as _uuid

    merchant = Merchant(
        slug=f"hook-i-{_uuid().hex[:6]}",
        legal_name="Hook Idem",
        display_name="HI",
        referral_code=f"HI-{_uuid().hex[:6].upper()}",
    )
    db_session.add(merchant)
    await db_session.commit()
    await db_session.refresh(merchant)

    db_session.add(Wallet(merchant_id=merchant.id, balance=Decimal("1000")))
    db_session.add(
        Transaction(
            merchant_id=merchant.id,
            amount=Decimal("1000"),
            status="successful",
            razorpay_order_id="order_DOUBLE",
            gateway_ref="pay_DOUBLE",
        )
    )
    await db_session.commit()

    payload = json.dumps({
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_DOUBLE",
                    "order_id": "order_DOUBLE",
                    "amount": 100000,
                    "method": "card",
                }
            }
        },
    }).encode("utf-8")

    with patch("app.routers.webhooks.verify_webhook_signature", return_value=True):
        r = await client.post(
            "/api/v1/webhooks/razorpay",
            content=payload,
            headers={
                "Content-Type": "application/json",
                "X-Razorpay-Signature": "mocked",
            },
        )
    assert r.status_code == 200

    res = await db_session.execute(select(Wallet).where(Wallet.merchant_id == merchant.id))
    wallet = res.scalar_one()
    assert float(wallet.balance) == 1000.0  # NOT double-credited


@pytest.mark.asyncio
async def test_webhook_ignores_unknown_event_types(client):
    payload = json.dumps({"event": "subscription.activated", "payload": {}}).encode("utf-8")
    with patch("app.routers.webhooks.verify_webhook_signature", return_value=True):
        r = await client.post(
            "/api/v1/webhooks/razorpay",
            content=payload,
            headers={
                "Content-Type": "application/json",
                "X-Razorpay-Signature": "mocked",
            },
        )
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_webhook_unknown_order_returns_404(client):
    payload = json.dumps({
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_GHOST",
                    "order_id": "order_GHOST",
                    "amount": 5000,
                    "method": "card",
                }
            }
        },
    }).encode("utf-8")
    with patch("app.routers.webhooks.verify_webhook_signature", return_value=True):
        r = await client.post(
            "/api/v1/webhooks/razorpay",
            content=payload,
            headers={
                "Content-Type": "application/json",
                "X-Razorpay-Signature": "mocked",
            },
        )
    assert r.status_code == 404

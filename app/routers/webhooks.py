"""Razorpay webhook handler — no auth, signature-verified.

The webhook is the authoritative crediting path; the /wallet/topup/confirm
endpoint exists for fast UX feedback. Both paths converge on the same
"mark txn successful + credit wallet" logic and are idempotent via
transactions.gateway_ref UNIQUE.
"""
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select

from app.core.logging import get_logger
from app.models.wallet import Transaction, Wallet
from app.services.razorpay_client import verify_webhook_signature
from app.utils.dependencies import DBSession

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
log = get_logger("app.routers.webhooks")


@router.post("/razorpay", status_code=status.HTTP_200_OK)
async def razorpay_webhook(request: Request, db: DBSession) -> dict:
    raw_body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")

    if not verify_webhook_signature(raw_body, signature):
        log.warning("razorpay_webhook_bad_signature")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid signature"
        )

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON")

    event = payload.get("event")
    if event != "payment.captured":
        log.info("razorpay_webhook_ignored", webhook_event=event)
        return {"status": "ignored", "event": event}

    payment_entity = (
        payload.get("payload", {}).get("payment", {}).get("entity", {})
    )
    payment_id = payment_entity.get("id")
    order_id = payment_entity.get("order_id")
    amount_paise = payment_entity.get("amount")
    method = payment_entity.get("method")

    if not (payment_id and order_id and amount_paise is not None):
        raise HTTPException(status_code=400, detail="malformed payment payload")

    res = await db.execute(
        select(Transaction).where(Transaction.razorpay_order_id == order_id)
    )
    txn = res.scalar_one_or_none()
    if not txn:
        log.warning("razorpay_webhook_unknown_order", order_id=order_id)
        raise HTTPException(status_code=404, detail="transaction not found")

    if txn.status == "successful" and txn.gateway_ref == payment_id:
        log.info("razorpay_webhook_duplicate", order_id=order_id)
        return {"status": "duplicate", "order_id": order_id}

    if txn.status == "successful":
        return {"status": "already_credited", "order_id": order_id}

    txn.status = "successful"
    txn.gateway_ref = payment_id
    txn.payment_method = method

    res = await db.execute(select(Wallet).where(Wallet.merchant_id == txn.merchant_id))
    wallet = res.scalar_one_or_none()
    if not wallet:
        log.error("razorpay_webhook_wallet_missing", merchant_id=str(txn.merchant_id))
        raise HTTPException(status_code=500, detail="wallet missing for merchant")

    wallet.balance = wallet.balance + txn.amount
    wallet.last_recharged_at = datetime.now(timezone.utc)
    if wallet.status == "depleted":
        wallet.status = "active"

    await db.commit()
    log.info(
        "razorpay_webhook_credited",
        merchant_id=str(txn.merchant_id),
        amount=float(txn.amount),
        balance=float(wallet.balance),
    )
    return {"status": "credited", "order_id": order_id, "balance": float(wallet.balance)}

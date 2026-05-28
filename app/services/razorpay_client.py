"""Razorpay client wrapper.

All SDK calls go through this module so tests can patch the client.
Three helpers:

  - create_order(amount_inr, receipt) -> dict
  - verify_payment_signature(...)
  - verify_webhook_signature(...)

Amounts are passed in rupees; the SDK requires paise — we multiply by 100.
"""
from __future__ import annotations

import hashlib
import hmac

import razorpay  # type: ignore

from app.core.config import get_settings


def _get_razorpay_client():
    settings = get_settings()
    if not (settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET):
        raise RuntimeError(
            "Razorpay credentials not configured "
            "(set RAZORPAY_KEY_ID + RAZORPAY_KEY_SECRET)"
        )
    return razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))


def _get_key_secret() -> str:
    return get_settings().RAZORPAY_KEY_SECRET


def _get_webhook_secret() -> str:
    return get_settings().RAZORPAY_WEBHOOK_SECRET


def create_order(amount_inr: float, receipt: str) -> dict:
    """Create a Razorpay order. amount_inr is in rupees; SDK takes paise."""
    client = _get_razorpay_client()
    return client.order.create(
        data={
            "amount": int(round(amount_inr * 100)),
            "currency": "INR",
            "receipt": receipt,
            "payment_capture": 1,
        }
    )


def verify_payment_signature(order_id: str, payment_id: str, signature: str) -> bool:
    """HMAC_SHA256('<order_id>|<payment_id>', key_secret)."""
    body = f"{order_id}|{payment_id}".encode("utf-8")
    expected = hmac.new(
        _get_key_secret().encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def verify_webhook_signature(raw_body: bytes, signature: str) -> bool:
    """HMAC_SHA256(raw_body, webhook_secret)."""
    expected = hmac.new(
        _get_webhook_secret().encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)

from unittest.mock import MagicMock, patch

import pytest


def test_create_order_returns_razorpay_order_id():
    from app.services import razorpay_client as rc_module

    mock_client = MagicMock()
    mock_client.order.create.return_value = {"id": "order_ABCDEF", "amount": 100000, "currency": "INR"}

    with patch.object(rc_module, "_get_razorpay_client", return_value=mock_client):
        order = rc_module.create_order(amount_inr=1000, receipt="txn_xyz")

    assert order["id"] == "order_ABCDEF"
    mock_client.order.create.assert_called_once()
    args, kwargs = mock_client.order.create.call_args
    payload = kwargs.get("data") if "data" in kwargs else args[0]
    assert payload["amount"] == 100000
    assert payload["currency"] == "INR"
    assert payload["receipt"] == "txn_xyz"


def test_verify_payment_signature_accepts_valid():
    import hmac
    import hashlib

    from app.services import razorpay_client as rc_module

    order_id = "order_ABC"
    payment_id = "pay_XYZ"
    secret = "test_secret"

    body = f"{order_id}|{payment_id}".encode("utf-8")
    expected_sig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

    with patch.object(rc_module, "_get_key_secret", return_value=secret):
        assert rc_module.verify_payment_signature(order_id, payment_id, expected_sig) is True


def test_verify_payment_signature_rejects_tampered():
    from app.services import razorpay_client as rc_module

    with patch.object(rc_module, "_get_key_secret", return_value="test_secret"):
        assert rc_module.verify_payment_signature("order_ABC", "pay_XYZ", "wrong_sig") is False


def test_verify_webhook_signature_accepts_valid():
    import hmac
    import hashlib

    from app.services import razorpay_client as rc_module

    secret = "webhook_secret"
    body = b'{"event":"payment.captured","payload":{}}'
    expected_sig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

    with patch.object(rc_module, "_get_webhook_secret", return_value=secret):
        assert rc_module.verify_webhook_signature(body, expected_sig) is True


def test_verify_webhook_signature_rejects_tampered():
    from app.services import razorpay_client as rc_module

    with patch.object(rc_module, "_get_webhook_secret", return_value="webhook_secret"):
        assert rc_module.verify_webhook_signature(b'{"x":1}', "wrong") is False

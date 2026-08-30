from unittest.mock import AsyncMock
import uuid

import pytest


async def _create_shop(auth_client):
    response = await auth_client.post(
        "/api/v1/merchants/",
        json={"legal_name": "Acme Furniture Pvt Ltd", "display_name": "Acme"},
    )
    assert response.status_code == 201, response.text
    merchant_id = response.json()["id"]
    auth_client.headers["X-Merchant-Id"] = merchant_id
    return merchant_id


@pytest.mark.asyncio
async def test_pan_and_gstin_complete_provider_backed_kyc(
    auth_client, db_session, monkeypatch
):
    from app.routers import merchant_verification, merchants
    from app.models.merchant import Merchant
    from app.services.sandbox_client import SandboxGstinResult, SandboxPanResult
    from sqlalchemy import select

    merchant_id = await _create_shop(auth_client)
    fake_client = AsyncMock()
    fake_client.verify_pan.return_value = SandboxPanResult(
        transaction_id="pan-tx-1",
        category="company",
        status="valid",
        remarks=None,
        name_match=True,
        date_of_birth_match=True,
    )
    fake_client.verify_gstin.return_value = SandboxGstinResult(
        transaction_id="gst-tx-1",
        gstin="27ABCDE1234F1Z5",
        legal_name="ACME FURNITURE PRIVATE LIMITED",
        business_nature="Retail Business",
        state_name="Maharashtra",
        state_code="27",
        pan="ABCDE1234F",
        registration_start_date="01/07/2017",
        registration_status="Active",
        valid_gstin=True,
    )
    monkeypatch.setattr(merchant_verification, "get_sandbox_client", lambda: fake_client)
    monkeypatch.setattr(merchants, "process_referral_payout", AsyncMock())
    monkeypatch.setattr(merchants, "process_kyc_welcome_bonus", AsyncMock())

    pan_response = await auth_client.post(
        f"/api/v1/merchants/{merchant_id}/verification/pan",
        json={
            "pan": "abcde1234f",
            "name_as_per_pan": "Acme Furniture Pvt Ltd",
            "date_of_birth": "2015-08-20",
            "consent": True,
        },
    )
    assert pan_response.status_code == 200, pan_response.text
    assert pan_response.json()["pan"]["masked_pan"] == "******234F"
    assert pan_response.json()["is_kyc_completed"] is False
    assert "ABCDE1234F" not in pan_response.text

    gstin_response = await auth_client.post(
        f"/api/v1/merchants/{merchant_id}/verification/gstin",
        json={"gstin": "27abcde1234f1z5"},
    )
    assert gstin_response.status_code == 200, gstin_response.text
    body = gstin_response.json()
    assert body["gstin"]["status"] == "verified"
    assert body["gstin"]["legal_name"] == "ACME FURNITURE PRIVATE LIMITED"
    assert body["is_kyc_completed"] is True

    completed_result = await db_session.execute(
        select(Merchant.is_kyc_completed).where(Merchant.id == uuid.UUID(merchant_id))
    )
    assert completed_result.scalar_one() is True
    merchants.process_referral_payout.assert_awaited_once()
    merchants.process_kyc_welcome_bonus.assert_awaited_once()


@pytest.mark.asyncio
async def test_gstin_must_belong_to_verified_pan(auth_client, monkeypatch):
    from app.routers import merchant_verification
    from app.services.sandbox_client import SandboxGstinResult, SandboxPanResult

    merchant_id = await _create_shop(auth_client)
    fake_client = AsyncMock()
    fake_client.verify_pan.return_value = SandboxPanResult(
        transaction_id="pan-tx-2",
        category="company",
        status="valid",
        remarks=None,
        name_match=True,
        date_of_birth_match=True,
    )
    fake_client.verify_gstin.return_value = SandboxGstinResult(
        transaction_id="gst-tx-2",
        gstin="27ABCDE1234F1Z5",
        legal_name="OTHER BUSINESS LIMITED",
        business_nature="Retail Business",
        state_name="Maharashtra",
        state_code="27",
        pan="ZZZZZ9999Z",
        registration_start_date="01/07/2017",
        registration_status="Active",
        valid_gstin=True,
    )
    monkeypatch.setattr(merchant_verification, "get_sandbox_client", lambda: fake_client)

    pan_response = await auth_client.post(
        f"/api/v1/merchants/{merchant_id}/verification/pan",
        json={
            "pan": "ABCDE1234F",
            "name_as_per_pan": "Acme Furniture Pvt Ltd",
            "date_of_birth": "2015-08-20",
            "consent": True,
        },
    )
    assert pan_response.status_code == 200

    response = await auth_client.post(
        f"/api/v1/merchants/{merchant_id}/verification/gstin",
        json={"gstin": "27ABCDE1234F1Z5"},
    )
    assert response.status_code == 400
    assert "does not belong" in response.json()["detail"]


@pytest.mark.asyncio
async def test_merchant_cannot_self_assert_kyc(auth_client):
    merchant_id = await _create_shop(auth_client)
    response = await auth_client.patch(
        f"/api/v1/merchants/{merchant_id}",
        json={"is_kyc_completed": True},
    )
    assert response.status_code == 403, response.text
    assert response.json()["detail"] == (
        "Shop verification is required before using merchant features."
    )

import uuid
from copy import deepcopy

import pytest
from pydantic import ValidationError


def _submission(email: str):
    return {
        "personal": {
            "full_name": "Asha Mehta",
            "email": email,
            "phone": "+919876543210",
            "relationship": "director",
        },
        "business": {
            "business_type": "private_limited",
            "business_name": "Acme Home",
            "registered_business_name": "Acme Furniture Private Limited",
            "business_pan": "ABCDE1234F",
            "registered_address": {
                "line1": "101 Market Road",
                "city": "Mumbai",
                "state": "Maharashtra",
                "postal_code": "400001",
            },
        },
        "shop": {
            "shop_name": "Acme Home Fort",
            "shop_address": {
                "line1": "9 Fort Lane",
                "city": "Mumbai",
                "state": "Maharashtra",
                "postal_code": "400001",
            },
            "gstin": "27ABCDE1234F1Z5",
            "operating_location": "Fort, Mumbai",
            "contact_number": "+919876543210",
            "operating_hours": "Mon-Sat, 10 AM-8 PM",
            "service_radius_km": 25,
        },
        "fulfilment": {
            "methods": ["merchant_delivery", "customer_pickup"],
            "delivery_service_radius_km": 25,
            "estimated_fulfilment_time": 4,
        },
        "information_accurate": True,
    }


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("shop", "service_radius_km", 100),
        ("fulfilment", "delivery_service_radius_km", 100),
        ("fulfilment", "estimated_fulfilment_time", 100),
        ("fulfilment", "estimated_fulfilment_time", "three days"),
    ],
)
def test_onboarding_numeric_fields_are_limited_to_two_digits(section, field, value):
    from app.schemas.merchant_onboarding import MerchantOnboardingSubmission

    payload = deepcopy(_submission("merchant@example.com"))
    payload[section][field] = value

    with pytest.raises(ValidationError):
        MerchantOnboardingSubmission.model_validate(payload)


@pytest.mark.asyncio
async def test_onboarding_is_sanitized_locked_and_required_for_activation(
    auth_client, db_session, test_user
):
    from app.models.merchant import Merchant
    from app.models.merchant_verification import GstinVerification, PanVerification
    from app.routers.merchant_verification import _pan_fingerprint

    test_user.is_email_verified = True
    test_user.phone = "+919876543210"
    await db_session.commit()

    created = await auth_client.post(
        "/api/v1/merchants/",
        json={"legal_name": "Acme Draft", "display_name": "Acme Draft"},
    )
    assert created.status_code == 201, created.text
    merchant_id = created.json()["id"]
    auth_client.headers["X-Merchant-Id"] = merchant_id

    submitted = await auth_client.post(
        f"/api/v1/merchants/{merchant_id}/onboarding/submit",
        json=_submission(test_user.email),
    )
    assert submitted.status_code == 200, submitted.text
    body = submitted.json()
    stored_business = body["settings"]["onboarding_submission"]["business"]
    assert stored_business["business_pan_masked"] == "******234F"
    assert "business_pan" not in stored_business
    assert body["settings"]["approval_status"] == "pending_verification"

    tamper = await auth_client.patch(
        f"/api/v1/merchants/{merchant_id}",
        json={"settings": {"approval_status": "approved", "onboarding_completed": False}},
    )
    assert tamper.status_code == 403, tamper.text
    assert "verification" in tamper.json()["detail"].lower()

    before_kyc = await auth_client.post(
        f"/api/v1/merchants/{merchant_id}/verification/agreements/accept",
        json={
            "merchant_agreement": True,
            "terms_and_conditions": True,
            "privacy_policy": True,
            "marketplace_rules": True,
            "product_listing_policy": True,
            "cancellation_return_rules": True,
            "merchant_obligations_and_fees": True,
        },
    )
    assert before_kyc.status_code == 409

    merchant = await db_session.get(Merchant, uuid.UUID(merchant_id))
    merchant.is_kyc_completed = True
    db_session.add(
        PanVerification(
            user_id=test_user.id,
            pan_fingerprint=_pan_fingerprint("ABCDE1234F"),
            pan_last_four="234F",
            verified_name="Acme Furniture Private Limited",
            category="company",
        )
    )
    db_session.add(
        GstinVerification(
            merchant_id=merchant.id,
            gstin="27ABCDE1234F1Z5",
            legal_name="ACME FURNITURE PRIVATE LIMITED",
            registration_status="Active",
        )
    )
    await db_session.commit()

    approved = await auth_client.post(
        f"/api/v1/merchants/{merchant_id}/verification/agreements/accept",
        json={
            "merchant_agreement": True,
            "terms_and_conditions": True,
            "privacy_policy": True,
            "marketplace_rules": True,
            "product_listing_policy": True,
            "cancellation_return_rules": True,
            "merchant_obligations_and_fees": True,
        },
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["approval_status"] == "approved"
    assert approved.json()["agreement"]["accepted"] is True

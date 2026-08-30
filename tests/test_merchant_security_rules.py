import re
import uuid

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/merchant/products/",
        "/api/v1/merchant/wallet/",
        "/api/v1/merchant/support/tickets/",
        "/api/v1/merchant/analytics/summary",
        "/api/v1/merchant/leads/",
        "/api/v1/merchant/contacts/",
        "/api/v1/merchant/buyer-intelligence/",
    ],
)
async def test_unverified_shop_cannot_access_merchant_features(auth_client, path):
    merchant = await auth_client.post(
        "/api/v1/merchants/",
        json={"legal_name": "Locked Shop", "display_name": "Locked Shop"},
    )

    response = await auth_client.get(
        path,
        headers={"X-Merchant-Id": merchant.json()["id"]},
    )

    assert response.status_code == 403
    assert "verification" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_support_ticket_has_unique_six_character_reference(
    auth_client, verify_merchant
):
    merchant = await auth_client.post(
        "/api/v1/merchants/",
        json={"legal_name": "Support Shop", "display_name": "Support Shop"},
    )
    merchant_id = merchant.json()["id"]
    await verify_merchant(merchant_id)

    created = await auth_client.post(
        "/api/v1/merchant/support/tickets/",
        headers={"X-Merchant-Id": merchant_id},
        json={
            "reason": "account_verification",
            "sub_reason": "gst_verification",
            "description": "Please review the submitted verification documents.",
        },
    )

    assert created.status_code == 201, created.text
    reference = created.json()["reference"]
    assert re.fullmatch(r"[A-Z0-9]{6}", reference)

    fetched = await auth_client.get(
        f"/api/v1/merchant/support/tickets/{reference}",
        headers={"X-Merchant-Id": merchant_id},
    )
    assert fetched.status_code == 200
    assert fetched.json()["id"] == created.json()["id"]


@pytest.mark.asyncio
async def test_shop_id_resolves_only_storefront_listed_products(
    auth_client, db_session, verify_merchant
):
    from app.models.merchant_product import MerchantProduct

    merchant = await auth_client.post(
        "/api/v1/merchants/",
        json={"legal_name": "QR Shop", "display_name": "QR Shop"},
    )
    body = merchant.json()
    await verify_merchant(body["id"])

    visible = MerchantProduct(
        merchant_id=uuid.UUID(body["id"]),
        sku="VISIBLE-1",
        title="Visible Product",
        status="published",
        has_simulafly_listing=True,
    )
    hidden = MerchantProduct(
        merchant_id=uuid.UUID(body["id"]),
        sku="HIDDEN-1",
        title="Removed Product",
        status="published",
        has_simulafly_listing=False,
    )
    db_session.add_all([visible, hidden])
    await db_session.commit()

    response = await auth_client.get(
        f"/api/v1/merchants/public/{body['shop_id']}/products"
    )

    assert response.status_code == 200, response.text
    assert [item["sku"] for item in response.json()] == ["VISIBLE-1"]

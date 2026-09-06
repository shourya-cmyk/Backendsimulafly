from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select


@pytest.mark.asyncio
async def test_buyer_intelligence_unlock_uses_configured_fifty_rupee_rate(
    auth_client,
    test_user,
    db_session,
):
    from app.models.buyer_intelligence import MerchantBuyerAccess
    from app.models.event import BuyerEvent, LedgerEntry
    from app.models.merchant import MemberRole, Merchant, MerchantMember
    from app.models.user import User
    from app.models.wallet import PricingRule, Wallet

    merchant = Merchant(
        slug="unlock-fifty",
        legal_name="Unlock Fifty",
        display_name="Unlock Fifty",
        referral_code="UNLOCK-50",
        is_kyc_completed=True,
    )
    buyer = User(email="unlock-buyer@example.com", full_name="Unlock Buyer")
    db_session.add_all([merchant, buyer])
    await db_session.flush()
    db_session.add_all(
        [
            MerchantMember(
                merchant_id=merchant.id,
                user_id=test_user.id,
                role=MemberRole.OWNER.value,
            ),
            Wallet(merchant_id=merchant.id, balance=Decimal("100")),
            PricingRule(
                event_type="lead_unlocked",
                merchant_id=merchant.id,
                rate=Decimal("50"),
                rate_type="fixed",
                effective_from=datetime.now(timezone.utc),
            ),
            BuyerEvent(
                merchant_id=merchant.id,
                user_id=buyer.id,
                event_type="click",
                context={},
            ),
        ]
    )
    await db_session.commit()

    list_response = await auth_client.get(
        "/api/v1/merchant/buyer-intelligence/",
        headers={"X-Merchant-Id": str(merchant.id)},
    )
    assert list_response.status_code == 200, list_response.text
    assert list_response.json()["unlock_cost"] == 50
    assert list_response.json()["items"][0]["unlock_cost"] == 50

    response = await auth_client.post(
        f"/api/v1/merchant/buyer-intelligence/{buyer.id}/unlock",
        headers={"X-Merchant-Id": str(merchant.id)},
    )
    assert response.status_code == 200, response.text
    assert response.json()["unlock_cost"] == 50

    wallet = (
        await db_session.execute(
            select(Wallet).where(Wallet.merchant_id == merchant.id)
        )
    ).scalar_one()
    access = (
        await db_session.execute(
            select(MerchantBuyerAccess).where(
                MerchantBuyerAccess.merchant_id == merchant.id,
                MerchantBuyerAccess.user_id == buyer.id,
            )
        )
    ).scalar_one()
    ledger = (
        await db_session.execute(
            select(LedgerEntry).where(
                LedgerEntry.merchant_id == merchant.id,
                LedgerEntry.reason == "buyer_intel_unlock",
            )
        )
    ).scalar_one()

    assert wallet.balance == Decimal("50")
    assert Decimal(str(access.unlock_cost)) == Decimal("50")
    assert ledger.amount == Decimal("-50")

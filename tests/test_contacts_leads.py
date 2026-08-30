import pytest
import uuid
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from app.models.buyer_intelligence import MerchantContact
from app.models.lead import BuyerLead, LeadStatus
from app.models.user import User
from app.models.merchant import Merchant, MerchantMember, MemberRole


@pytest.mark.asyncio
async def test_merged_contacts_and_leads(auth_client, test_user, db_session):
    # 1. Create a merchant
    m = Merchant(
        slug="test-merchant-crm",
        legal_name="CRM Test Merchant",
        display_name="CRM Merchant",
        referral_code="SIMULA-CRM-1",
        is_kyc_completed=True,
    )
    db_session.add(m)
    await db_session.commit()
    await db_session.refresh(m)

    # 2. Add current user as member of merchant
    member = MerchantMember(
        merchant_id=m.id,
        user_id=test_user.id,
        role=MemberRole.OWNER.value,
    )
    db_session.add(member)
    await db_session.commit()

    # 3. Create an offline MerchantContact
    contact_offline = MerchantContact(
        merchant_id=m.id,
        name="Offline Customer",
        phone="+91 99999 88888",
        email="offline@example.com",
        source="csv",
        last_purchase_note="Last month",
        invite_status="invited",
    )
    db_session.add(contact_offline)

    # 4. Create a buyer user and a converted BuyerLead
    buyer_user = User(
        email="buyer@example.com",
        full_name="Converted Buyer",
        phone="+91 77777 66666",
    )
    db_session.add(buyer_user)
    await db_session.commit()
    await db_session.refresh(buyer_user)

    lead = BuyerLead(
        merchant_id=m.id,
        user_id=buyer_user.id,
        lead_type="direct_purchase",
        status=LeadStatus.CONVERTED.value,
        converted_at=datetime.now(timezone.utc) - timedelta(days=2),
        delivery_phone="+91 77777 66666",
    )
    db_session.add(lead)
    await db_session.commit()
    await db_session.refresh(lead)

    # 5. Call contacts list endpoint
    headers = {
        "X-Merchant-Id": str(m.id),
    }
    r = await auth_client.get("/api/v1/merchant/contacts/", headers=headers)
    assert r.status_code == 200, r.text
    data = r.json()

    assert data["total"] == 2
    items = data["items"]
    names = [x["name"] for x in items]
    assert "Offline Customer" in names
    assert "Converted Buyer" in names

    # Verify fields of Converted Buyer
    buyer_item = next(x for x in items if x["name"] == "Converted Buyer")
    assert buyer_item["source"] == "Checkout"
    assert buyer_item["invite_status"] == "joined"
    assert buyer_item["last_purchase_note"] == "This week"
    assert buyer_item["phone"] == "+91 77777 66666"

    # 6. Test search filter
    r_search = await auth_client.get("/api/v1/merchant/contacts/?search=Converted", headers=headers)
    assert r_search.status_code == 200
    search_data = r_search.json()
    assert search_data["total"] == 1
    assert search_data["items"][0]["name"] == "Converted Buyer"

    # 7. Test status filter
    r_status = await auth_client.get("/api/v1/merchant/contacts/?invite_status=invited", headers=headers)
    assert r_status.status_code == 200
    status_data = r_status.json()
    assert status_data["total"] == 1
    assert status_data["items"][0]["name"] == "Offline Customer"


@pytest.mark.asyncio
async def test_bulk_offer_campaign(auth_client, test_user, db_session):
    # 1. Create a merchant and member
    m = Merchant(
        slug="test-merchant-bulk",
        legal_name="Bulk Test Merchant",
        display_name="Bulk Merchant",
        referral_code="SIMULA-BULK-1",
        is_kyc_completed=True,
    )
    db_session.add(m)
    await db_session.commit()
    await db_session.refresh(m)

    member = MerchantMember(
        merchant_id=m.id,
        user_id=test_user.id,
        role=MemberRole.OWNER.value,
    )
    db_session.add(member)
    await db_session.commit()

    # 2. Create target offline contacts
    contact1 = MerchantContact(
        merchant_id=m.id,
        name="Target Customer 1",
        phone="+91 99999 11111",
        source="csv",
        invite_status="not_invited",
    )
    contact2 = MerchantContact(
        merchant_id=m.id,
        name="Target Customer 2",
        phone="+91 99999 22222",
        source="csv",
        invite_status="not_invited",
    )
    db_session.add_all([contact1, contact2])
    await db_session.commit()
    await db_session.refresh(contact1)
    await db_session.refresh(contact2)

    # 3. Call bulk-offer endpoint
    payload = {
        "contact_ids": [str(contact1.id), str(contact2.id)],
        "products": ["Premium Wooden Chair", "Luxurious Fabric Sofa"],
        "discount": 25,
        "max_customers": 100,
        "max_days": 14,
        "message": "Hi, claim your 25% discount now!",
    }
    headers = {
        "X-Merchant-Id": str(m.id),
    }
    r = await auth_client.post("/api/v1/merchant/contacts/bulk-offer", json=payload, headers=headers)
    assert r.status_code == 201, r.text
    data = r.json()
    assert "campaign_id" in data
    assert data["sent_count"] == 2

    # 4. Check if invite status of contacts was updated in DB
    contacts_stmt = select(MerchantContact).where(MerchantContact.id.in_([contact1.id, contact2.id]))
    updated_contacts = (await db_session.execute(contacts_stmt)).scalars().all()
    assert len(updated_contacts) == 2
    for c in updated_contacts:
        assert c.invite_status == "invited"

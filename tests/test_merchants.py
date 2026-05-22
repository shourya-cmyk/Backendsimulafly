import uuid

import pytest
from sqlalchemy import select


@pytest.mark.asyncio
async def test_create_merchant_persists_required_fields(db_session, test_user):
    from app.models.merchant import Merchant, MerchantMember, MemberRole

    m = Merchant(
        slug="acme-furniture",
        legal_name="Acme Furniture Co.",
        display_name="Acme",
        country="IN",
        referral_code="SIMULA-ACME-2026",
    )
    db_session.add(m)
    await db_session.commit()
    await db_session.refresh(m)

    assert isinstance(m.id, uuid.UUID)
    assert m.status == "active"
    assert m.settings == {}
    assert m.created_at is not None


@pytest.mark.asyncio
async def test_merchant_member_unique_per_merchant_user_pair(db_session, test_user):
    from app.models.merchant import Merchant, MerchantMember, MemberRole

    m = Merchant(
        slug="dup-test",
        legal_name="Dup",
        display_name="Dup",
        referral_code="DUP-1",
    )
    db_session.add(m)
    await db_session.commit()

    mm1 = MerchantMember(merchant_id=m.id, user_id=test_user.id, role=MemberRole.OWNER.value)
    db_session.add(mm1)
    await db_session.commit()

    mm2 = MerchantMember(merchant_id=m.id, user_id=test_user.id, role=MemberRole.STAFF.value)
    db_session.add(mm2)
    with pytest.raises(Exception):  # IntegrityError from UNIQUE constraint
        await db_session.commit()
    await db_session.rollback()

import uuid

import pytest
from fastapi import HTTPException

from app.utils.merchant_context import (
    MerchantContext,
    get_current_merchant,
    require_role,
)


@pytest.mark.asyncio
async def test_get_current_merchant_returns_context_for_member(db_session, test_user):
    from app.models.merchant import Merchant, MerchantMember, MemberRole

    m = Merchant(slug="ctx-test", legal_name="Ctx", display_name="Ctx", referral_code="CTX-1")
    db_session.add(m)
    await db_session.commit()
    await db_session.refresh(m)

    mm = MerchantMember(merchant_id=m.id, user_id=test_user.id, role=MemberRole.OWNER.value)
    db_session.add(mm)
    await db_session.commit()

    ctx = await get_current_merchant(user=test_user, x_merchant_id=m.id, db=db_session)
    assert isinstance(ctx, MerchantContext)
    assert ctx.merchant.id == m.id
    assert ctx.role == "owner"


@pytest.mark.asyncio
async def test_get_current_merchant_403_for_non_member(db_session, test_user):
    from app.models.merchant import Merchant

    m = Merchant(slug="other", legal_name="Other", display_name="Other", referral_code="OTH-1")
    db_session.add(m)
    await db_session.commit()
    await db_session.refresh(m)

    with pytest.raises(HTTPException) as exc:
        await get_current_merchant(user=test_user, x_merchant_id=m.id, db=db_session)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_get_current_merchant_404_for_unknown_merchant_id(db_session, test_user):
    with pytest.raises(HTTPException) as exc:
        await get_current_merchant(
            user=test_user, x_merchant_id=uuid.uuid4(), db=db_session
        )
    assert exc.value.status_code == 404


def test_require_role_accepts_listed_roles():
    from app.models.merchant import Merchant

    fake_merchant = Merchant(slug="x", legal_name="x", display_name="x", referral_code="X-1")
    fake_member = type("FM", (), {"role": "admin"})()
    ctx = MerchantContext(merchant=fake_merchant, member=fake_member, role="admin")

    guard = require_role("owner", "admin")
    # Should not raise
    guard(ctx)


def test_require_role_403_when_role_not_in_list():
    from app.models.merchant import Merchant

    fake_merchant = Merchant(slug="x", legal_name="x", display_name="x", referral_code="X-1")
    fake_member = type("FM", (), {"role": "staff"})()
    ctx = MerchantContext(merchant=fake_merchant, member=fake_member, role="staff")

    guard = require_role("owner", "admin")
    with pytest.raises(HTTPException) as exc:
        guard(ctx)
    assert exc.value.status_code == 403

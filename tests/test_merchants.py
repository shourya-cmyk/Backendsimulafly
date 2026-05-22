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


def test_merchant_create_validates_required_fields():
    from app.schemas.merchant import MerchantCreate
    from pydantic import ValidationError

    # Valid input
    valid = MerchantCreate(legal_name="Acme Furniture Co.", display_name="Acme")
    assert valid.country == "IN"  # default
    assert valid.support_email is None

    # Missing required field
    with pytest.raises(ValidationError):
        MerchantCreate(legal_name="Acme")  # missing display_name


def test_merchant_member_invite_validates_email_and_role():
    from app.schemas.merchant import MemberInvite
    from pydantic import ValidationError

    valid = MemberInvite(email="sarah@acme.com", role="admin")
    assert valid.role == "admin"

    with pytest.raises(ValidationError):
        MemberInvite(email="not-an-email", role="admin")

    with pytest.raises(ValidationError):
        MemberInvite(email="ok@acme.com", role="not-a-role")


@pytest.mark.asyncio
async def test_onboarding_creates_merchant_with_owner_membership(auth_client, test_user, db_session):
    from app.models.merchant import Merchant, MerchantMember

    r = await auth_client.post(
        "/api/v1/merchants/",
        json={"legal_name": "Acme Furniture Co.", "display_name": "Acme"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["legal_name"] == "Acme Furniture Co."
    assert body["slug"] == "acme-furniture-co"
    assert body["referral_code"].startswith("SIMULA-")

    # Owner membership row created
    res = await db_session.execute(select(MerchantMember).where(MerchantMember.user_id == test_user.id))
    members = list(res.scalars().all())
    assert len(members) == 1
    assert members[0].role == "owner"


@pytest.mark.asyncio
async def test_onboarding_handles_slug_collision(auth_client, test_user, db_session):
    # Create first merchant
    r1 = await auth_client.post(
        "/api/v1/merchants/",
        json={"legal_name": "Acme Furniture Co.", "display_name": "Acme 1"},
    )
    assert r1.status_code == 201
    slug1 = r1.json()["slug"]

    # Create second with same legal_name — must produce a different slug
    r2 = await auth_client.post(
        "/api/v1/merchants/",
        json={"legal_name": "Acme Furniture Co.", "display_name": "Acme 2"},
    )
    assert r2.status_code == 201
    slug2 = r2.json()["slug"]
    assert slug1 != slug2
    assert slug2.startswith("acme-furniture-co-")


@pytest.mark.asyncio
async def test_list_my_merchants_returns_only_my_memberships(auth_client, test_user):
    # Create two merchants under this user
    for name in ("First Org", "Second Org"):
        r = await auth_client.post(
            "/api/v1/merchants/", json={"legal_name": name, "display_name": name}
        )
        assert r.status_code == 201

    r = await auth_client.get("/api/v1/merchants/me")
    assert r.status_code == 200
    body = r.json()
    names = sorted(m["legal_name"] for m in body)
    assert names == ["First Org", "Second Org"]


@pytest.mark.asyncio
async def test_get_merchant_requires_membership(auth_client, db_session):
    from app.models.merchant import Merchant

    foreign = Merchant(
        slug="foreign", legal_name="Foreign", display_name="Foreign", referral_code="FOR-1"
    )
    db_session.add(foreign)
    await db_session.commit()
    await db_session.refresh(foreign)

    r = await auth_client.get(
        f"/api/v1/merchants/{foreign.id}", headers={"X-Merchant-Id": str(foreign.id)}
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_patch_merchant_requires_owner_or_admin(auth_client, test_user, db_session):
    from app.models.merchant import Merchant, MerchantMember, MemberRole

    m = Merchant(slug="patch", legal_name="Patch", display_name="Patch", referral_code="PCH-1")
    db_session.add(m)
    await db_session.commit()
    await db_session.refresh(m)

    # Staff role — not allowed to PATCH
    db_session.add(MerchantMember(merchant_id=m.id, user_id=test_user.id, role=MemberRole.STAFF.value))
    await db_session.commit()

    r = await auth_client.patch(
        f"/api/v1/merchants/{m.id}",
        headers={"X-Merchant-Id": str(m.id)},
        json={"display_name": "New Name"},
    )
    assert r.status_code == 403

from decimal import Decimal

import pytest


@pytest.mark.asyncio
async def test_resolve_rate_returns_global_default_when_no_override(db_session):
    from app.services.pricing import resolve_rate
    from app.models.wallet import PricingRule
    from datetime import datetime, timezone
    import uuid

    db_session.add(
        PricingRule(
            id=uuid.uuid4(),
            event_type="click",
            merchant_id=None,
            rate=Decimal("0.25"),
            rate_type="fixed",
            currency="INR",
            effective_from=datetime.now(timezone.utc),
        )
    )
    await db_session.commit()

    rate, rate_type = await resolve_rate(db_session, "click", uuid.uuid4())
    assert float(rate) == 0.25
    assert rate_type == "fixed"


@pytest.mark.asyncio
async def test_resolve_rate_returns_merchant_override(db_session):
    from app.services.pricing import resolve_rate
    from app.models.merchant import Merchant
    from app.models.wallet import PricingRule
    from datetime import datetime, timezone
    import uuid

    m = Merchant(slug="po", legal_name="Pricing Override", display_name="PO", referral_code="PO-1")
    db_session.add(m)
    await db_session.commit()
    await db_session.refresh(m)

    db_session.add(
        PricingRule(
            id=uuid.uuid4(),
            event_type="click",
            merchant_id=None,
            rate=Decimal("0.25"),
            rate_type="fixed",
            effective_from=datetime.now(timezone.utc),
        )
    )
    db_session.add(
        PricingRule(
            id=uuid.uuid4(),
            event_type="click",
            merchant_id=m.id,
            rate=Decimal("0.10"),
            rate_type="fixed",
            effective_from=datetime.now(timezone.utc),
        )
    )
    await db_session.commit()

    rate, rate_type = await resolve_rate(db_session, "click", m.id)
    assert float(rate) == 0.10


@pytest.mark.asyncio
async def test_resolve_rate_returns_zero_when_unknown_event_type(db_session):
    from app.services.pricing import resolve_rate
    import uuid

    rate, rate_type = await resolve_rate(db_session, "nonexistent_event", uuid.uuid4())
    assert float(rate) == 0.0
    assert rate_type == "fixed"


@pytest.mark.asyncio
async def test_resolve_rate_skips_expired_rules(db_session):
    from app.services.pricing import resolve_rate
    from app.models.wallet import PricingRule
    from datetime import datetime, timedelta, timezone
    import uuid

    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    db_session.add(
        PricingRule(
            id=uuid.uuid4(),
            event_type="click",
            merchant_id=None,
            rate=Decimal("0.99"),
            rate_type="fixed",
            effective_from=yesterday - timedelta(days=10),
            effective_until=yesterday,
        )
    )
    db_session.add(
        PricingRule(
            id=uuid.uuid4(),
            event_type="click",
            merchant_id=None,
            rate=Decimal("0.25"),
            rate_type="fixed",
            effective_from=datetime.now(timezone.utc) - timedelta(hours=1),
        )
    )
    await db_session.commit()

    rate, _ = await resolve_rate(db_session, "click", uuid.uuid4())
    assert float(rate) == 0.25

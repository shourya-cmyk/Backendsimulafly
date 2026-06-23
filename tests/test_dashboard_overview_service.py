"""Unit tests for DashboardOverviewService (executive overview + activity).

These exercise the read-only aggregation service directly against the in-memory
SQLite test database, mirroring the style of ``tests/test_wallet_admin_service``.
They verify the response *shapes* (every group + field present) and that every
count is a non-negative integer — both on an empty database and after seeding a
small set of representative rows whose counts are asserted exactly.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.schemas.admin.overview import ActivityEntry, DashboardOverview
from app.services.admin.dashboard_overview_service import DashboardOverviewService

# Every snapshot group and its integer fields, used to assert shape + types.
_GROUP_FIELDS: dict[str, tuple[str, ...]] = {
    "merchant_health": (
        "total",
        "active",
        "suspended",
        "low_balance_wallets",
        "frozen_wallets",
    ),
    "store_health": ("total", "active", "inactive", "suspended"),
    "support": ("open", "pending", "resolved", "sla_breaches"),
    "invoices": ("total", "unpaid", "paid", "overdue"),
    "trust_safety": ("open_fraud_alerts", "resolved_fraud_alerts"),
    "referral": ("referred_users", "total_users"),
    "funnel": ("impressions", "clicks", "add_to_cart", "purchases"),
    "redeem": ("active_codes", "redeemed_codes"),
}


def _assert_overview_shape(overview: DashboardOverview) -> None:
    """Every group is present and every field is a non-negative int."""
    data = overview.model_dump()
    for group, fields in _GROUP_FIELDS.items():
        assert group in data, f"missing group {group!r}"
        for field in fields:
            value = data[group][field]
            assert isinstance(value, int), f"{group}.{field} not int: {value!r}"
            assert value >= 0, f"{group}.{field} negative: {value!r}"


@pytest.mark.asyncio
async def test_overview_shape_on_empty_db(db_session):
    overview = await DashboardOverviewService(db_session).overview()
    assert isinstance(overview, DashboardOverview)
    _assert_overview_shape(overview)
    # Empty database → all zero.
    assert overview.merchant_health.total == 0
    assert overview.funnel.impressions == 0


@pytest.mark.asyncio
async def test_overview_counts_seeded_rows(db_session, test_user):
    from app.models.admin import FraudAlert, FraudAlertStatus
    from app.models.cart import CartItem
    from app.models.event import BuyerEvent, EventType
    from app.models.invoice import Invoice, InvoiceStatus
    from app.models.lead import BuyerLead, Order, OrderStatus
    from app.models.merchant import Merchant, MerchantStatus
    from app.models.merchant_product import MerchantProduct
    from app.models.redeem_code import RedeemCode, RedeemCodeStatus
    from app.models.store import Store, StoreStatus
    from app.models.support import SupportTicket, SupportTicketStatus
    from app.models.user import User
    from app.models.wallet import Wallet, WalletStatus

    now = datetime.now(timezone.utc)
    past = now - timedelta(days=2)

    # 2 merchants: one active, one suspended.
    m_active = Merchant(
        slug=f"ov-a-{uuid.uuid4().hex[:6]}",
        legal_name="Active Co",
        display_name="AC",
        referral_code=f"AC-{uuid.uuid4().hex[:6].upper()}",
        status=MerchantStatus.ACTIVE.value,
    )
    m_susp = Merchant(
        slug=f"ov-s-{uuid.uuid4().hex[:6]}",
        legal_name="Susp Co",
        display_name="SC",
        referral_code=f"SC-{uuid.uuid4().hex[:6].upper()}",
        status=MerchantStatus.SUSPENDED.value,
    )
    db_session.add_all([m_active, m_susp])
    await db_session.commit()
    await db_session.refresh(m_active)
    await db_session.refresh(m_susp)

    # Wallets: one low-balance (< 100 default threshold), one frozen.
    db_session.add_all(
        [
            Wallet(merchant_id=m_active.id, balance=Decimal("10")),
            Wallet(merchant_id=m_susp.id, balance=Decimal("500"),
                   status=WalletStatus.FROZEN.value),
        ]
    )
    # Stores: active, inactive, suspended.
    db_session.add_all(
        [
            Store(merchant_id=m_active.id, name="S1", status=StoreStatus.ACTIVE.value),
            Store(merchant_id=m_active.id, name="S2", status=StoreStatus.INACTIVE.value),
            Store(merchant_id=m_susp.id, name="S3", status=StoreStatus.SUSPENDED.value),
        ]
    )
    # Support tickets: open, pending, resolved, and one breaching SLA.
    db_session.add_all(
        [
            SupportTicket(subject="open", requester_type="merchant",
                          requester_id=m_active.id, status=SupportTicketStatus.OPEN.value),
            SupportTicket(subject="pending", requester_type="merchant",
                          requester_id=m_active.id, status=SupportTicketStatus.PENDING.value),
            SupportTicket(subject="resolved", requester_type="merchant",
                          requester_id=m_active.id, status=SupportTicketStatus.RESOLVED.value),
            SupportTicket(subject="breach", requester_type="merchant",
                          requester_id=m_active.id, status=SupportTicketStatus.OPEN.value,
                          sla_due_at=past),
        ]
    )
    # Invoices: paid, unpaid (future due), unpaid+overdue (past due).
    db_session.add_all(
        [
            Invoice(merchant_id=m_active.id, number=f"INV-{uuid.uuid4().hex[:6]}",
                    amount=Decimal("100"), status=InvoiceStatus.PAID.value,
                    due_date=now + timedelta(days=5)),
            Invoice(merchant_id=m_active.id, number=f"INV-{uuid.uuid4().hex[:6]}",
                    amount=Decimal("100"), status=InvoiceStatus.UNPAID.value,
                    due_date=now + timedelta(days=5)),
            Invoice(merchant_id=m_active.id, number=f"INV-{uuid.uuid4().hex[:6]}",
                    amount=Decimal("100"), status=InvoiceStatus.UNPAID.value,
                    due_date=past),
        ]
    )
    # Fraud alerts: one open, one resolved.
    db_session.add_all(
        [
            FraudAlert(subject_type="merchant", subject_id=str(m_active.id),
                       reason="suspicious", status=FraudAlertStatus.OPEN.value),
            FraudAlert(subject_type="merchant", subject_id=str(m_susp.id),
                       reason="reviewed", status=FraudAlertStatus.RESOLVED.value),
        ]
    )
    # Referred user (the conftest test_user has no referral code).
    db_session.add(
        User(
            email=f"ref-{uuid.uuid4().hex[:8]}@example.com",
            full_name="Referred",
            referred_by_code="SOMECODE",
        )
    )
    # Redeem codes: active + redeemed.
    db_session.add_all(
        [
            RedeemCode(code=f"RC-{uuid.uuid4().hex[:8]}", value=Decimal("50"),
                       status=RedeemCodeStatus.ACTIVE.value),
            RedeemCode(code=f"RC-{uuid.uuid4().hex[:8]}", value=Decimal("50"),
                       status=RedeemCodeStatus.REDEEMED.value),
        ]
    )
    await db_session.commit()

    # Funnel: products + events + cart + completed order.
    product = MerchantProduct(
        merchant_id=m_active.id, sku="OV-1", title="P1", status="published"
    )
    db_session.add(product)
    await db_session.commit()
    await db_session.refresh(product)

    for _ in range(3):
        db_session.add(BuyerEvent(user_id=test_user.id, merchant_id=m_active.id,
                                  merchant_product_id=product.id,
                                  event_type=EventType.IMPRESSION.value, context={}))
    db_session.add(BuyerEvent(user_id=test_user.id, merchant_id=m_active.id,
                              merchant_product_id=product.id,
                              event_type=EventType.CLICK.value, context={}))
    db_session.add(CartItem(user_id=test_user.id, merchant_product_id=product.id, quantity=1))
    lead = BuyerLead(merchant_id=m_active.id, user_id=test_user.id,
                     lead_type="direct_purchase")
    db_session.add(lead)
    await db_session.commit()
    await db_session.refresh(lead)
    db_session.add(Order(lead_id=lead.id, merchant_id=m_active.id, user_id=test_user.id,
                         status=OrderStatus.COMPLETED.value))
    await db_session.commit()

    overview = await DashboardOverviewService(db_session).overview(now=now)
    _assert_overview_shape(overview)

    assert overview.merchant_health.total == 2
    assert overview.merchant_health.active == 1
    assert overview.merchant_health.suspended == 1
    assert overview.merchant_health.low_balance_wallets == 1
    assert overview.merchant_health.frozen_wallets == 1

    assert overview.store_health.total == 3
    assert overview.store_health.active == 1
    assert overview.store_health.inactive == 1
    assert overview.store_health.suspended == 1

    assert overview.support.open == 2  # "open" + "breach" are both open
    assert overview.support.pending == 1
    assert overview.support.resolved == 1
    assert overview.support.sla_breaches == 1

    assert overview.invoices.total == 3
    assert overview.invoices.unpaid == 2
    assert overview.invoices.paid == 1
    assert overview.invoices.overdue == 1

    assert overview.trust_safety.open_fraud_alerts == 1
    assert overview.trust_safety.resolved_fraud_alerts == 1

    assert overview.referral.referred_users == 1
    assert overview.referral.total_users == 2  # test_user + referred user

    assert overview.funnel.impressions == 3
    assert overview.funnel.clicks == 1
    assert overview.funnel.add_to_cart == 1
    assert overview.funnel.purchases == 1

    assert overview.redeem.active_codes == 1
    assert overview.redeem.redeemed_codes == 1


@pytest.mark.asyncio
async def test_activity_returns_recent_audit_entries_newest_first(db_session):
    from app.models.admin import AuditLog

    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    for i in range(15):
        db_session.add(
            AuditLog(
                action=f"action.{i}",
                target_type="merchant",
                target_id=str(i),
                outcome="success",
                audit_metadata={},
                created_at=base + timedelta(minutes=i),
            )
        )
    await db_session.commit()

    entries = await DashboardOverviewService(db_session).activity(limit=12)
    assert len(entries) == 12
    assert all(isinstance(e, ActivityEntry) for e in entries)
    # Newest first: action.14 down to action.3.
    assert entries[0].action == "action.14"
    assert entries[-1].action == "action.3"
    # created_at strictly non-increasing.
    times = [e.created_at for e in entries]
    assert times == sorted(times, reverse=True)


@pytest.mark.asyncio
async def test_activity_empty_db_returns_empty_list(db_session):
    entries = await DashboardOverviewService(db_session).activity()
    assert entries == []

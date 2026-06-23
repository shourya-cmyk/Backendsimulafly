"""Unit tests for WalletAdminService (Requirement 14).

These exercise the admin wallet list/transaction-history/adjustment logic
directly against the in-memory test database, mirroring the style of
``tests/test_billing_service.py``. They verify the credit/debit primitives
reuse (balance + Transaction + LedgerEntry) and the over-debit invariant
(HTTP 422, balance unchanged — R14.5).
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.event import LedgerEntry
from app.models.merchant import Merchant
from app.models.wallet import Transaction, Wallet, WalletStatus
from app.schemas.admin.wallets import AdjustmentDirection
from app.services.admin.wallet_admin_service import WalletAdminService


async def _make_merchant_wallet(
    db_session,
    *,
    slug: str,
    balance: Decimal,
    status: str = WalletStatus.ACTIVE.value,
) -> Wallet:
    m = Merchant(
        slug=slug,
        legal_name=f"{slug} Legal",
        display_name=f"{slug} Display",
        referral_code=f"{slug}-REF",
    )
    db_session.add(m)
    await db_session.commit()
    await db_session.refresh(m)
    wallet = Wallet(merchant_id=m.id, balance=balance, status=status)
    db_session.add(wallet)
    await db_session.commit()
    await db_session.refresh(wallet)
    return wallet


@pytest.mark.asyncio
async def test_credit_increases_balance_and_records_transaction_and_ledger(db_session):
    wallet = await _make_merchant_wallet(db_session, slug="wcred", balance=Decimal("100"))
    svc = WalletAdminService(db_session)

    updated, txn, ledger = await svc.adjust(
        wallet.id, direction=AdjustmentDirection.CREDIT, amount=Decimal("50")
    )

    assert float(updated.balance) == 150.0
    # Transaction recorded (R14.3)
    assert float(txn.amount) == 50.0
    assert txn.merchant_id == wallet.merchant_id
    # LedgerEntry mirrors the credit path
    assert ledger.entry_type == "credit"
    assert float(ledger.amount) == 50.0
    assert float(ledger.balance_after) == 150.0
    assert ledger.related_txn_id == txn.id

    rows = (
        await db_session.execute(
            select(Transaction).where(Transaction.merchant_id == wallet.merchant_id)
        )
    ).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_credit_reactivates_depleted_wallet(db_session):
    wallet = await _make_merchant_wallet(
        db_session, slug="wdep", balance=Decimal("0"), status=WalletStatus.DEPLETED.value
    )
    svc = WalletAdminService(db_session)

    updated, _txn, _ledger = await svc.adjust(
        wallet.id, direction=AdjustmentDirection.CREDIT, amount=Decimal("25")
    )

    assert updated.status == WalletStatus.ACTIVE.value
    assert updated.last_recharged_at is not None


@pytest.mark.asyncio
async def test_debit_within_balance_decreases_and_records_deduction(db_session):
    wallet = await _make_merchant_wallet(db_session, slug="wdeb", balance=Decimal("100"))
    svc = WalletAdminService(db_session)

    updated, txn, ledger = await svc.adjust(
        wallet.id, direction=AdjustmentDirection.DEBIT, amount=Decimal("30")
    )

    assert float(updated.balance) == 70.0
    # Mirrors BillingService._deduct: negative signed amount + "deduction" type.
    assert ledger.entry_type == "deduction"
    assert float(ledger.amount) == -30.0
    assert float(ledger.balance_after) == 70.0
    assert float(txn.amount) == -30.0
    assert ledger.related_txn_id == txn.id


@pytest.mark.asyncio
async def test_over_debit_rejected_422_and_balance_unchanged(db_session):
    wallet = await _make_merchant_wallet(db_session, slug="wover", balance=Decimal("40"))
    svc = WalletAdminService(db_session)

    with pytest.raises(HTTPException) as exc:
        await svc.adjust(
            wallet.id, direction=AdjustmentDirection.DEBIT, amount=Decimal("40.0001")
        )
    assert exc.value.status_code == 422

    # No mutation occurred: balance unchanged, no Transaction / LedgerEntry written.
    refreshed = (
        await db_session.execute(select(Wallet).where(Wallet.id == wallet.id))
    ).scalar_one()
    assert float(refreshed.balance) == 40.0
    txns = (
        await db_session.execute(
            select(Transaction).where(Transaction.merchant_id == wallet.merchant_id)
        )
    ).scalars().all()
    ledgers = (
        await db_session.execute(
            select(LedgerEntry).where(LedgerEntry.wallet_id == wallet.id)
        )
    ).scalars().all()
    assert txns == []
    assert ledgers == []


@pytest.mark.asyncio
async def test_debit_exactly_equal_to_balance_allowed(db_session):
    wallet = await _make_merchant_wallet(db_session, slug="weq", balance=Decimal("40"))
    svc = WalletAdminService(db_session)

    updated, _txn, _ledger = await svc.adjust(
        wallet.id, direction=AdjustmentDirection.DEBIT, amount=Decimal("40")
    )
    assert float(updated.balance) == 0.0


@pytest.mark.asyncio
async def test_adjust_unknown_wallet_404(db_session):
    import uuid

    svc = WalletAdminService(db_session)
    with pytest.raises(HTTPException) as exc:
        await svc.adjust(
            uuid.uuid4(), direction=AdjustmentDirection.CREDIT, amount=Decimal("10")
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_list_below_threshold_filters_low_balance_wallets(db_session):
    from app.core.config import get_settings

    threshold = get_settings().ADMIN_WALLET_RISK_THRESHOLD
    low = await _make_merchant_wallet(
        db_session, slug="wlow", balance=threshold - Decimal("1")
    )
    await _make_merchant_wallet(db_session, slug="whigh", balance=threshold + Decimal("100"))

    svc = WalletAdminService(db_session)
    page, names = await svc.list_wallets(below_threshold=True)

    ids = {w.id for w in page.items}
    assert low.id in ids
    assert all(w.balance < threshold for w in page.items)
    # Merchant name map is populated for the returned wallets.
    assert names.get(low.merchant_id) == "wlow Display"


@pytest.mark.asyncio
async def test_list_transactions_ordered_desc(db_session):
    import asyncio

    wallet = await _make_merchant_wallet(db_session, slug="wtx", balance=Decimal("1000"))
    svc = WalletAdminService(db_session)

    await svc.adjust(wallet.id, direction=AdjustmentDirection.CREDIT, amount=Decimal("10"))
    await asyncio.sleep(0.01)
    await svc.adjust(wallet.id, direction=AdjustmentDirection.DEBIT, amount=Decimal("5"))

    page = await svc.list_transactions(wallet.id)
    assert page.total == 2
    created = [t.created_at for t in page.items]
    assert created == sorted(created, reverse=True)

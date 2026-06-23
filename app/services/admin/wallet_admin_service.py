"""Merchant-wallet admin service — list, transaction history, adjustments (R14).

Backs ``app/routers/admin/wallets.py``. This service operates over the existing
:class:`app.models.wallet.Wallet` / :class:`app.models.wallet.Transaction` /
:class:`app.models.event.LedgerEntry` tables and **reuses the existing wallet
credit/debit primitives** rather than duplicating billing logic:

* A **debit** mirrors :meth:`app.services.billing.BillingService._deduct`: it
  decrements ``Wallet.balance`` and writes a ``LedgerEntry`` with
  ``entry_type="deduction"`` and a negative signed ``amount`` whose
  ``balance_after`` records the post-adjustment balance.
* A **credit** mirrors the Razorpay webhook / referral credit path
  (``app/routers/webhooks.py``, ``app/routers/merchants.py``): it increments
  ``Wallet.balance`` and writes a ``LedgerEntry`` with ``entry_type="credit"``
  and a positive signed ``amount``.

Both directions additionally record a ``Transaction`` row (R14.3, R14.4) linked
from the ledger entry via ``related_txn_id``, keeping the wallet/transaction
ledger a single source of truth. Over-debit (amount > balance) is rejected with
HTTP 422 **before any mutation**, so the balance is left unchanged (R14.5).

Operations:

* :meth:`WalletAdminService.list_wallets` — paginated listing including the
  owning merchant, balance, currency, status, and low-balance threshold
  (R14.1); optional filter to wallets below the configured risk threshold
  (R14.2).
* :meth:`WalletAdminService.list_transactions` — a single wallet's transaction
  history, paginated and ordered by ``created_at`` descending (R14.7).
* :meth:`WalletAdminService.adjust` — apply a credit/debit adjustment (R14.3,
  R14.4, R14.5). The caller (router) records the audit entry (R14.6).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.event import LedgerEntry
from app.models.merchant import Merchant
from app.models.wallet import Transaction, TransactionStatus, Wallet, WalletStatus
from app.schemas.admin.wallets import AdjustmentDirection
from app.services.admin.listing import ListParams, Page, paginate

log = get_logger("app.services.admin.wallet_admin_service")

#: Reason stamped on the LedgerEntry / Transaction produced by an admin adjustment.
_ADJUSTMENT_REASON = "admin_adjustment"

#: Whitelisted sortable columns for the wallet directory (R14 / R20.4).
_SORTABLE: dict[str, ColumnElement] = {
    "balance": Wallet.balance,
    "status": Wallet.status,
    "low_balance_threshold": Wallet.low_balance_threshold,
    "created_at": Wallet.created_at,
    "last_recharged_at": Wallet.last_recharged_at,
}


class WalletAdminService:
    """List/history/adjust operations over the existing wallet tables."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_wallets(
        self,
        *,
        page: int = 1,
        page_size: int | None = None,
        sort: str | None = None,
        below_threshold: bool = False,
    ) -> tuple[Page, dict[uuid.UUID, str]]:
        """Return a page of wallets and a merchant-id → display-name map (R14.1, R14.2).

        When ``below_threshold`` is set, only wallets whose balance is strictly
        below the configured risk threshold (``ADMIN_WALLET_RISK_THRESHOLD``,
        default ₹100) are returned (R14.2). The merchant-name map lets the router
        project each wallet's owning merchant without a relationship on the
        ``Wallet`` model.
        """
        stmt = select(Wallet)
        if below_threshold:
            threshold = get_settings().ADMIN_WALLET_RISK_THRESHOLD
            stmt = stmt.where(Wallet.balance < threshold)

        params = ListParams(
            page=page,
            page_size=page_size if page_size is not None else ListParams().page_size,
            sort=sort,
        )
        page_obj = await paginate(
            self.db,
            stmt,
            params=params,
            sortable=_SORTABLE,
            searchable=(),
        )

        merchant_names = await self._merchant_name_map(
            [w.merchant_id for w in page_obj.items]
        )
        return page_obj, merchant_names

    async def list_transactions(
        self,
        wallet_id: uuid.UUID,
        *,
        page: int = 1,
        page_size: int | None = None,
    ) -> Page:
        """Return a wallet's transaction history, newest first (R14.7).

        Transactions are looked up by the wallet's ``merchant_id`` (a wallet has
        exactly one merchant) and ordered by ``created_at`` descending. Raises
        ``404`` when the wallet id is unknown.
        """
        wallet = await self._get_wallet_or_404(wallet_id)
        params = ListParams(
            page=page,
            page_size=page_size if page_size is not None else ListParams().page_size,
            sort="-created_at",
        )
        return await paginate(
            self.db,
            select(Transaction).where(Transaction.merchant_id == wallet.merchant_id),
            params=params,
            sortable={"created_at": Transaction.created_at},
            searchable=(),
        )

    async def adjust(
        self,
        wallet_id: uuid.UUID,
        *,
        direction: AdjustmentDirection,
        amount: Decimal,
    ) -> tuple[Wallet, Transaction, LedgerEntry]:
        """Apply a credit or debit adjustment to a wallet (R14.3, R14.4, R14.5).

        A **credit** increases the balance; a **debit** decreases it. An attempt
        to debit more than the current balance is rejected with HTTP 422 and
        leaves the balance unchanged — the over-balance check runs before any
        mutation, so no partial state is written (R14.5). Both directions record
        a ``Transaction`` and a ``LedgerEntry`` mirroring the existing
        credit/deduction primitives. Raises ``404`` when the wallet is unknown.

        Returns the updated wallet plus the recorded transaction and ledger
        entry. The router records the corresponding audit entry (R14.6).
        """
        if amount <= 0:
            # Defensive: request schema already enforces amount > 0.
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="amount must be positive",
            )

        wallet = await self._get_wallet_or_404(wallet_id)

        if direction is AdjustmentDirection.DEBIT and amount > wallet.balance:
            # Reject over-debit BEFORE mutating anything (R14.5).
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="debit amount exceeds wallet balance",
            )

        is_credit = direction is AdjustmentDirection.CREDIT
        signed = amount if is_credit else -amount

        # Record the transaction (R14.3, R14.4). gateway="system" mirrors the
        # existing internal credit path (referral / KYC bonuses, webhooks).
        txn = Transaction(
            merchant_id=wallet.merchant_id,
            amount=signed,
            currency=wallet.currency,
            payment_method=_ADJUSTMENT_REASON,
            gateway="system",
            status=TransactionStatus.SUCCESSFUL.value,
            gateway_ref=f"ADJ-{uuid.uuid4()}",
        )
        self.db.add(txn)
        await self.db.flush()

        # Apply the balance change using the same arithmetic as the existing
        # credit path (balance + amount) / BillingService._deduct (balance - amount).
        wallet.balance = wallet.balance + signed

        if is_credit:
            # Mirror the webhook/referral credit path: stamp the recharge time
            # and reactivate a previously depleted wallet.
            from datetime import datetime, timezone

            wallet.last_recharged_at = datetime.now(timezone.utc)
            if wallet.status == WalletStatus.DEPLETED.value:
                wallet.status = WalletStatus.ACTIVE.value

        ledger = LedgerEntry(
            merchant_id=wallet.merchant_id,
            wallet_id=wallet.id,
            related_txn_id=txn.id,
            entry_type="credit" if is_credit else "deduction",
            amount=signed,
            reason=_ADJUSTMENT_REASON,
            balance_after=wallet.balance,
        )
        self.db.add(ledger)

        await self.db.commit()
        await self.db.refresh(wallet)
        await self.db.refresh(txn)
        await self.db.refresh(ledger)

        log.info(
            "admin_wallet_adjustment",
            wallet_id=str(wallet.id),
            merchant_id=str(wallet.merchant_id),
            direction=direction.value,
            amount=str(amount),
            balance_after=str(wallet.balance),
        )
        return wallet, txn, ledger

    # -- Internal helpers --------------------------------------------------

    async def _get_wallet_or_404(self, wallet_id: uuid.UUID) -> Wallet:
        wallet = (
            await self.db.execute(select(Wallet).where(Wallet.id == wallet_id))
        ).scalar_one_or_none()
        if wallet is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="wallet not found",
            )
        return wallet

    async def _merchant_name_map(
        self, merchant_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, str]:
        if not merchant_ids:
            return {}
        rows = (
            await self.db.execute(
                select(Merchant.id, Merchant.display_name).where(
                    Merchant.id.in_(set(merchant_ids))
                )
            )
        ).all()
        return {row[0]: row[1] for row in rows}

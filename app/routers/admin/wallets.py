"""Admin merchant-wallet router (Requirement 14).

Endpoints (all prefixed ``/admin``; mounted under ``/api/v1`` by the wiring
step in task 16.1):

| Method | Path                          | Permission       | Req            |
|--------|-------------------------------|------------------|----------------|
| GET    | `/wallets`                    | `wallets.read`   | 14.1, 14.2     |
| GET    | `/wallets/{id}/transactions`  | `wallets.read`   | 14.7           |
| POST   | `/wallets/{id}/adjust`        | `wallets.adjust` | 14.3–14.6      |

Reads are gated by ``require_permission("wallets.read")``; the adjustment action
by ``require_permission("wallets.adjust")``. The adjustment route is wrapped with
``audited(...)`` so each credit/debit writes one immutable audit entry capturing
the acting admin, the amount, and the affected wallet (R14.6 / R19.1). Over-debit
yields HTTP 422 with the balance unchanged (R14.5); unknown wallet ids yield
HTTP 404 from the service layer. This module reuses the existing
``Wallet``/``Transaction``/``LedgerEntry`` tables and credit/deduction primitives
via :class:`app.services.admin.wallet_admin_service.WalletAdminService`.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.admin.listing import ListingEnvelope
from app.schemas.admin.wallets import (
    WalletAdjustmentRequest,
    WalletAdjustmentResponse,
    WalletListItem,
    WalletTransactionItem,
)
from app.services.admin.audit_service import AuditContext, audited
from app.services.admin.wallet_admin_service import WalletAdminService
from app.utils.admin_dependencies import require_permission

router = APIRouter(prefix="/admin", tags=["admin-wallets"])

DBSession = Annotated[AsyncSession, Depends(get_db)]


@router.get(
    "/wallets",
    response_model=ListingEnvelope[WalletListItem],
    dependencies=[Depends(require_permission("wallets.read"))],
)
async def list_wallets(
    db: DBSession,
    page: int = Query(default=1, ge=1),
    page_size: int | None = Query(default=None, ge=1),
    sort: str | None = Query(default=None),
    below_threshold: bool = Query(
        default=False,
        description="Return only wallets whose balance is below the configured risk threshold",
    ),
) -> ListingEnvelope[WalletListItem]:
    """Paginated wallet directory incl. merchant, balance, currency, status,
    and low-balance threshold (R14.1); optional below-threshold filter (R14.2)."""
    page_obj, merchant_names = await WalletAdminService(db).list_wallets(
        page=page,
        page_size=page_size,
        sort=sort,
        below_threshold=below_threshold,
    )
    items = [
        WalletListItem(
            id=w.id,
            merchant_id=w.merchant_id,
            merchant_name=merchant_names.get(w.merchant_id),
            balance=w.balance,
            currency=w.currency,
            status=w.status,
            low_balance_threshold=w.low_balance_threshold,
            last_recharged_at=w.last_recharged_at,
            created_at=w.created_at,
        )
        for w in page_obj.items
    ]
    return ListingEnvelope[WalletListItem](
        items=items,
        page=page_obj.page,
        page_size=page_obj.page_size,
        total=page_obj.total,
        total_pages=page_obj.total_pages,
        has_next=page_obj.has_next,
        next_page=page_obj.next_page,
    )


@router.get(
    "/wallets/{wallet_id}/transactions",
    response_model=ListingEnvelope[WalletTransactionItem],
    dependencies=[Depends(require_permission("wallets.read"))],
)
async def list_wallet_transactions(
    wallet_id: uuid.UUID,
    db: DBSession,
    page: int = Query(default=1, ge=1),
    page_size: int | None = Query(default=None, ge=1),
) -> ListingEnvelope[WalletTransactionItem]:
    """Paginated transaction history for one wallet, ordered by creation date
    descending (R14.7); unknown wallet → 404."""
    page_obj = await WalletAdminService(db).list_transactions(
        wallet_id,
        page=page,
        page_size=page_size,
    )
    return ListingEnvelope[WalletTransactionItem](
        items=[WalletTransactionItem.model_validate(t) for t in page_obj.items],
        page=page_obj.page,
        page_size=page_obj.page_size,
        total=page_obj.total,
        total_pages=page_obj.total_pages,
        has_next=page_obj.has_next,
        next_page=page_obj.next_page,
    )


@router.post(
    "/wallets/{wallet_id}/adjust",
    response_model=WalletAdjustmentResponse,
    dependencies=[Depends(require_permission("wallets.adjust"))],
)
async def adjust_wallet(
    wallet_id: uuid.UUID,
    body: WalletAdjustmentRequest,
    db: DBSession,
    audit: Annotated[AuditContext, Depends(audited("wallets.adjust", "wallet"))],
) -> WalletAdjustmentResponse:
    """Credit or debit a wallet (R14.3, R14.4); over-debit → 422 unchanged
    (R14.5); audited with amount + affected wallet (R14.6)."""
    wallet, txn, ledger = await WalletAdminService(db).adjust(
        wallet_id,
        direction=body.direction,
        amount=body.amount,
    )
    audit.set_target(wallet.id)
    audit.add_metadata(
        direction=body.direction.value,
        amount=str(body.amount),
        balance_after=str(wallet.balance),
        transaction_id=str(txn.id),
    )
    return WalletAdjustmentResponse(
        wallet_id=wallet.id,
        merchant_id=wallet.merchant_id,
        direction=body.direction,
        amount=body.amount,
        balance=wallet.balance,
        currency=wallet.currency,
        status=wallet.status,
        transaction_id=txn.id,
        ledger_entry_id=ledger.id,
    )

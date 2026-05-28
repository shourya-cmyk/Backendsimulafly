import uuid

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from app.models.wallet import Transaction, Wallet
from app.schemas.wallet import (
    PaginatedTransactions,
    TransactionOut,
    WalletOut,
    WalletSettingsUpdate,
)
from app.utils.dependencies import DBSession
from app.utils.merchant_context import CurrentMerchantContext

router = APIRouter(prefix="/merchant/wallet", tags=["merchant-wallet"])


async def _get_wallet_or_404(db, merchant_id: uuid.UUID) -> Wallet:
    res = await db.execute(select(Wallet).where(Wallet.merchant_id == merchant_id))
    wallet = res.scalar_one_or_none()
    if not wallet:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="wallet not found for merchant"
        )
    return wallet


@router.get("/", response_model=WalletOut)
async def get_wallet(db: DBSession, ctx: CurrentMerchantContext) -> Wallet:
    return await _get_wallet_or_404(db, ctx.merchant.id)


@router.get("/transactions", response_model=PaginatedTransactions)
async def list_transactions(
    db: DBSession,
    ctx: CurrentMerchantContext,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    count_stmt = (
        select(func.count())
        .select_from(Transaction)
        .where(Transaction.merchant_id == ctx.merchant.id)
    )
    total = (await db.execute(count_stmt)).scalar_one()

    stmt = (
        select(Transaction)
        .where(Transaction.merchant_id == ctx.merchant.id)
        .order_by(Transaction.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return {"items": list(rows), "total": total, "limit": limit, "offset": offset}


@router.patch("/settings", response_model=WalletOut)
async def update_wallet_settings(
    body: WalletSettingsUpdate, db: DBSession, ctx: CurrentMerchantContext
) -> Wallet:
    wallet = await _get_wallet_or_404(db, ctx.merchant.id)

    data = body.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(wallet, k, v)
    await db.commit()
    await db.refresh(wallet)
    return wallet


from decimal import Decimal
from datetime import datetime, timezone

from app.core.config import get_settings
from app.schemas.wallet import (
    TopupConfirmRequest,
    TopupIntentRequest,
    TopupIntentResponse,
)
from app.services.razorpay_client import create_order, verify_payment_signature


def _get_razorpay_key_id() -> str:
    """Wrapped for testability."""
    return get_settings().RAZORPAY_KEY_ID


@router.post("/topup/intent", response_model=TopupIntentResponse)
async def topup_intent(
    body: TopupIntentRequest, db: DBSession, ctx: CurrentMerchantContext
) -> dict:
    # Create a pending Transaction first so we have an id for the receipt
    txn = Transaction(
        merchant_id=ctx.merchant.id,
        amount=Decimal(str(body.amount)),
        currency=body.currency,
        status="pending",
    )
    db.add(txn)
    await db.flush()

    try:
        order = create_order(amount_inr=body.amount, receipt=f"txn_{txn.id}")
    except Exception as e:  # noqa: BLE001
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"could not create Razorpay order: {e}",
        )

    txn.razorpay_order_id = order["id"]
    await db.commit()
    await db.refresh(txn)

    return {
        "order_id": order["id"],
        "razorpay_key_id": _get_razorpay_key_id(),
        "amount": body.amount,
        "currency": body.currency,
        "transaction_id": txn.id,
    }


@router.post("/topup/confirm", response_model=WalletOut)
async def topup_confirm(
    body: TopupConfirmRequest, db: DBSession, ctx: CurrentMerchantContext
) -> Wallet:
    if not verify_payment_signature(body.order_id, body.payment_id, body.signature):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="invalid signature"
        )

    res = await db.execute(
        select(Transaction).where(
            Transaction.razorpay_order_id == body.order_id,
            Transaction.merchant_id == ctx.merchant.id,
        )
    )
    txn = res.scalar_one_or_none()
    if not txn:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="transaction not found"
        )

    # Idempotency: if already successful with this gateway_ref, no-op
    if txn.status == "successful" and txn.gateway_ref == body.payment_id:
        return await _get_wallet_or_404(db, ctx.merchant.id)

    if txn.status == "successful":
        # Already credited via webhook with a different ref — return wallet as-is
        return await _get_wallet_or_404(db, ctx.merchant.id)

    # Mark transaction successful + credit wallet atomically
    txn.status = "successful"
    txn.gateway_ref = body.payment_id
    txn.razorpay_signature = body.signature

    wallet = await _get_wallet_or_404(db, ctx.merchant.id)
    wallet.balance = wallet.balance + txn.amount
    wallet.last_recharged_at = datetime.now(timezone.utc)
    if wallet.status == "depleted":
        wallet.status = "active"

    await db.commit()
    await db.refresh(wallet)
    return wallet

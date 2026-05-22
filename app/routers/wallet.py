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

import asyncio
import os
from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.models.merchant import Merchant
from app.models.wallet import Wallet


async def ensure_per_store_wallets(db: AsyncSession):
    print("Checking and populating per-store wallets for all merchants...")

    res = await db.execute(select(Merchant))
    merchants = list(res.scalars().all())

    created_count = 0
    existing_count = 0

    for m in merchants:
        w_res = await db.execute(select(Wallet).where(Wallet.merchant_id == m.id))
        wallet = w_res.scalar_one_or_none()
        if not wallet:
            new_wallet = Wallet(merchant_id=m.id, balance=Decimal("0.00"))
            db.add(new_wallet)
            created_count += 1
        else:
            existing_count += 1

    await db.commit()
    print(f"Done! Created {created_count} new per-store wallets. {existing_count} stores already had wallets.")


async def main():
    database_url = os.getenv("DATABASE_URL") or get_settings().DATABASE_URL
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    print("Connecting to DB...")
    engine = create_async_engine(database_url)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        await ensure_per_store_wallets(session)


if __name__ == "__main__":
    asyncio.run(main())

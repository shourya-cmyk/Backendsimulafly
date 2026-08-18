import asyncio
import os
import re
from sqlalchemy import select, update, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.models.merchant import Merchant, MerchantMember
from app.utils.id_generator import validate_mpuid, validate_mpsuid


async def backfill_merchant_ids(db: AsyncSession):
    print("Starting MPUID and MPSUID backfill for legacy merchants...")

    # First expand column lengths in database
    print("Expanding partner_id and shop_id column sizes to VARCHAR(32)...")
    await db.execute(text("ALTER TABLE merchants ALTER COLUMN partner_id TYPE VARCHAR(32);"))
    await db.execute(text("ALTER TABLE merchants ALTER COLUMN shop_id TYPE VARCHAR(32);"))
    await db.commit()

    # Fetch all merchants ordered by creation date
    res = await db.execute(select(Merchant).order_by(Merchant.created_at.asc()))
    merchants = list(res.scalars().all())

    if not merchants:
        print("No merchants found in database.")
        return

    # Group merchants by partner_id
    partner_groups: dict[str, list[Merchant]] = {}
    no_partner_merchants: list[Merchant] = []

    for m in merchants:
        if m.partner_id:
            partner_groups.setdefault(m.partner_id, []).append(m)
        else:
            no_partner_merchants.append(m)

    # Sort groups by earliest merchant creation date
    sorted_partner_keys = sorted(
        partner_groups.keys(),
        key=lambda k: partner_groups[k][0].created_at,
    )

    partner_seq_counter = 1
    updated_partner_count = 0
    updated_shop_count = 0

    # Maps old partner_id -> new MPUID
    old_to_new_mpuid: dict[str, str] = {}

    for old_pid in sorted_partner_keys:
        shops = partner_groups[old_pid]
        first_shop = shops[0]

        # Determine MPUID
        if validate_mpuid(old_pid):
            new_mpuid = old_pid
            # Extract sequence number from existing MPUID if valid
            match = re.search(r"SIM-M-[A-Z]{2}-(\d{6})-[A-Z0-9]", old_pid)
            if match:
                merchant_seq = match.group(1)
            else:
                merchant_seq = f"{partner_seq_counter:06d}"
                partner_seq_counter += 1
        else:
            merchant_seq = f"{partner_seq_counter:06d}"
            partner_seq_counter += 1
            new_mpuid = f"SIM-M-DL-{merchant_seq}-N"
            updated_partner_count += 1

        old_to_new_mpuid[old_pid] = new_mpuid

        # Update shops within this partner group
        for shop_index, shop in enumerate(shops, start=1):
            shop.partner_id = new_mpuid

            # Determine MPSUID
            if not validate_mpsuid(shop.shop_id or ""):
                shop_seq_str = f"{shop_index:02d}"
                shop.shop_id = f"SIM-S-{merchant_seq}-{shop_seq_str}-N"
                updated_shop_count += 1

    # Handle merchants with no partner_id
    for shop in no_partner_merchants:
        merchant_seq = f"{partner_seq_counter:06d}"
        partner_seq_counter += 1
        new_mpuid = f"SIM-M-DL-{merchant_seq}-N"
        shop.partner_id = new_mpuid
        shop.shop_id = f"SIM-S-{merchant_seq}-01-N"
        updated_partner_count += 1
        updated_shop_count += 1

    await db.commit()
    print(
        f"Backfill complete! Updated {updated_partner_count} partner IDs to MPUID and {updated_shop_count} shop IDs to MPSUID."
    )


async def main():
    database_url = os.getenv("DATABASE_URL") or get_settings().DATABASE_URL
    # Ensure async engine format
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    print(f"Connecting to DB...")
    engine = create_async_engine(database_url)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        await backfill_merchant_ids(session)


if __name__ == "__main__":
    asyncio.run(main())

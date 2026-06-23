import asyncio
import os
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

DATABASE_URL = "postgresql+asyncpg://simuladb:tech%40%23exe26@simuladb.postgres.database.azure.com:5432/postgres?ssl=require"

async def main():
    print("Starting database migration...")
    engine = create_async_engine(DATABASE_URL)
    async with engine.connect() as conn:
        # 1. Update users table
        print("Updating users table...")
        await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_email_verified BOOLEAN NOT NULL DEFAULT FALSE;"))
        await conn.execute(text("UPDATE users SET is_email_verified = TRUE WHERE is_email_verified = FALSE;"))
        await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS referred_by_code VARCHAR(40);"))
        
        # 2. Update merchants table
        print("Updating merchants table...")
        await conn.execute(text("ALTER TABLE merchants ADD COLUMN IF NOT EXISTS is_kyc_completed BOOLEAN NOT NULL DEFAULT FALSE;"))
        await conn.execute(text("ALTER TABLE merchants ADD COLUMN IF NOT EXISTS referred_by_code VARCHAR(40);"))
        await conn.execute(text("ALTER TABLE merchants ADD COLUMN IF NOT EXISTS referral_bonus_paid BOOLEAN NOT NULL DEFAULT FALSE;"))
        await conn.execute(text("ALTER TABLE merchants ADD COLUMN IF NOT EXISTS kyc_bonus_paid BOOLEAN NOT NULL DEFAULT FALSE;"))
        
        # 3. Create otps table
        print("Creating otps table...")
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS otps (
                id UUID PRIMARY KEY,
                target VARCHAR(255) NOT NULL,
                code VARCHAR(6) NOT NULL,
                expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
            );
        """))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_otps_target ON otps(target);"))
        
        await conn.commit()
        print("Migration completed successfully!")

if __name__ == "__main__":
    asyncio.run(main())

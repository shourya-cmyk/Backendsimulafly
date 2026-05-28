import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

DATABASE_URL = "postgresql+asyncpg://simuladb:tech%40%23exe26@simuladb.postgres.database.azure.com:5432/postgres?ssl=require"

async def main():
    engine = create_async_engine(DATABASE_URL)
    async with engine.connect() as conn:
        res = await conn.execute(text("UPDATE users SET is_active = True;"))
        await conn.commit()
        print("Updated all users to is_active=True")

asyncio.run(main())

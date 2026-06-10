import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

DATABASE_URL = "postgresql+asyncpg://simuladb:tech%40%23exe26@simuladb.postgres.database.azure.com:5432/postgres?ssl=require"

async def main():
    engine = create_async_engine(DATABASE_URL)
    async with engine.connect() as conn:
        print("--- LAST 5 BUYER LEADS ---")
        res = await conn.execute(text("SELECT id, delivery_city, delivery_phone, created_at FROM buyer_leads ORDER BY created_at DESC LIMIT 5;"))
        for row in res:
            print(f"ID: {row.id} | CITY: {row.delivery_city} | PHONE: {row.delivery_phone} | CREATED: {row.created_at}")
            
        print("\n--- LAST 5 ORDERS ---")
        res = await conn.execute(text("SELECT id, lead_id, delivery_address, created_at FROM orders ORDER BY created_at DESC LIMIT 5;"))
        for row in res:
            print(f"ID: {row.id} | LEAD_ID: {row.lead_id} | ADDR: {row.delivery_address} | CREATED: {row.created_at}")

asyncio.run(main())

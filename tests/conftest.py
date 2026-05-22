"""Shared fixtures for the test suite.

Uses aiosqlite in-memory. pgvector / real-vector queries are skipped or mocked.
Azure AI client is monkeypatched to return deterministic values.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator

os.environ.setdefault("ENV", "development")
os.environ.setdefault("SECRET_KEY", "t" * 40)
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("AZURE_AI_FOUNDRY_ENDPOINT", "")
os.environ.setdefault("AZURE_AI_FOUNDRY_API_KEY", "")

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

from app.core.database import Base, get_db  # noqa: E402
from app.core.security import create_access_token, hash_password  # noqa: E402
from app.main import app  # noqa: E402
from app.models.user import User  # noqa: E402


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def db_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    # SQLite doesn't support Vector or JSONB — convert those column types for tests.
    from sqlalchemy import Text
    from sqlalchemy.dialects.sqlite import JSON as SQLiteJSON
    from sqlalchemy.dialects.postgresql import JSONB

    # Iterate through all tables in Base.metadata and replace JSONB/Vector types
    for table in Base.metadata.tables.values():
        for column in table.columns:
            # Replace JSONB with SQLite JSON
            if isinstance(column.type, JSONB):
                column.type = SQLiteJSON()
            # Replace Vector with Text
            elif type(column.type).__name__ == 'Vector':
                column.type = Text()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine) -> AsyncIterator[AsyncSession]:
    SessionLocal = async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)
    async with SessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def test_user(db_session) -> User:
    user = User(
        email=f"test-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password=hash_password("password123"),
        full_name="Test User",
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def client(db_session) -> AsyncIterator[AsyncClient]:
    async def _override_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def auth_client(client, test_user) -> AsyncClient:
    token = create_access_token(str(test_user.id))
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client

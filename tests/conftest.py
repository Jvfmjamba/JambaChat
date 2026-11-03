import base64
import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("SCHEDULER_ENABLED", "false")
os.environ.setdefault("ENCRYPTION_KEYS", base64.urlsafe_b64encode(b"\x01" * 32).decode())
os.environ.setdefault("INDEX_KEY", base64.urlsafe_b64encode(b"\x02" * 32).decode())


@pytest.fixture
async def sessionmaker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    from jambachat.db.models import Base

    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()

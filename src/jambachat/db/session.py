from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from jambachat.config import get_settings


def tune_sqlite(engine: AsyncEngine) -> None:

    @event.listens_for(engine.sync_engine, "connect")
    def _connect(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.close()


@lru_cache
def get_engine() -> AsyncEngine:
    url = get_settings().database_url
    engine = create_async_engine(url, pool_pre_ping=True)
    if url.startswith("sqlite"):
        tune_sqlite(engine)
    return engine


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        yield session

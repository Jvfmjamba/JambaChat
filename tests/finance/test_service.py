from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jambachat.finance.provider import ItemInfo
from jambachat.finance.service import FinanceService, FinanceSyncRateLimitError
from jambachat.users.models import User
from tests.fakes import FakeFinanceProvider


async def _user(session: AsyncSession) -> User:
    user = User(phone="1", phone_hash="1")
    session.add(user)
    await session.flush()
    return user


async def test_connect_list_and_limit_manual_sync(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker() as session:
        user = await _user(session)
        service = FinanceService(
            FakeFinanceProvider(items={"item-1": ItemInfo("Banco", "UPDATED")})
        )
        connection = await service.connect(session, user, "item-1")

        assert (await service.list_connections(session, user)) == [connection]
        now = datetime(2026, 11, 6, tzinfo=UTC)
        service.allow_manual_sync(connection, now)
        with pytest.raises(FinanceSyncRateLimitError):
            service.allow_manual_sync(connection, now + timedelta(minutes=14))
        service.allow_manual_sync(connection, now + timedelta(minutes=15))

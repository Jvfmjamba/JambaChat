from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jambachat.expenses.models import Expense
from jambachat.finance.importer import FinanceImporter
from jambachat.finance.models import FinanceConnection
from jambachat.finance.provider import RawTransaction
from jambachat.users.models import User
from tests.fakes import FakeFinanceProvider


async def _data(session: AsyncSession) -> tuple[User, FinanceConnection]:
    user = User(phone="1", phone_hash="1", timezone="America/Sao_Paulo")
    session.add(user)
    await session.flush()
    connection = FinanceConnection(user_id=user.id, item_ref="item-1", institution="Banco")
    session.add(connection)
    await session.flush()
    return user, connection


async def test_importer_is_idempotent_and_ignores_pending_transfer_and_card_payment(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker() as session:
        user, connection = await _data(session)
        provider = FakeFinanceProvider(
            transactions={
                "item-1": [
                    RawTransaction(
                        "ok", date(2026, 11, 5), -1200, "Mercado", None, "expense", "POSTED", False
                    ),
                    RawTransaction(
                        "pending", date(2026, 11, 5), -1200, "x", None, "expense", "PENDING", False
                    ),
                    RawTransaction(
                        "transfer", date(2026, 11, 5), -1200, "x", None, "expense", "POSTED", True
                    ),
                    RawTransaction(
                        "invoice",
                        date(2026, 11, 5),
                        -1200,
                        "Pagamento de fatura",
                        None,
                        "expense",
                        "POSTED",
                        False,
                    ),
                ]
            }
        )
        importer = FinanceImporter(provider)
        now = datetime(2026, 11, 6, tzinfo=UTC)

        assert await importer.run(session, user, connection, now) == 1
        assert await importer.run(session, user, connection, now + timedelta(days=1)) == 0
        entries = list(await session.scalars(select(Expense)))
        assert [entry.external_id for entry in entries] == ["ok"]
        assert connection.last_synced_at == now + timedelta(days=1)


async def test_importer_only_advances_sync_after_a_complete_batch(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker() as session:
        user, connection = await _data(session)
        importer = FinanceImporter(
            FakeFinanceProvider(
                transactions={
                    "item-1": [
                        RawTransaction(
                            "invalid", date(2026, 11, 5), 0, "x", None, "expense", "POSTED", False
                        )
                    ]
                }
            )
        )

        with pytest.raises(IntegrityError):
            await importer.run(session, user, connection, datetime(2026, 11, 6, tzinfo=UTC))
        assert connection.last_synced_at is None

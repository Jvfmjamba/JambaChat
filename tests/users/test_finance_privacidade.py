import json
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.fakes import FakeFinanceProvider

from jambachat.expenses.models import Expense
from jambachat.finance.errors import FinanceError
from jambachat.finance.models import ExpenseLink, FinanceConnection, FinanceSuppression
from jambachat.users.export import export_user_data
from jambachat.users.models import User
from jambachat.users.repository import delete_user_data


async def _user(session: AsyncSession) -> User:
    user = User(phone_hash=uuid.uuid4().hex, phone="5511999999999")
    session.add(user)
    await session.flush()
    return user


async def _connection(session: AsyncSession, user: User) -> FinanceConnection:
    connection = FinanceConnection(
        user_id=user.id,
        item_ref="item-secreto-do-agregador",
        institution="Banco Exemplo",
        status="active",
        last_synced_at=datetime(2026, 11, 3, 12, tzinfo=UTC),
    )
    session.add(connection)
    await session.flush()
    return connection


async def _finance_data(session: AsyncSession, user: User) -> FinanceConnection:
    connection = await _connection(session, user)
    imported = Expense(
        user_id=user.id,
        amount_cents=100,
        description="Importado",
        category="Outros",
    )
    matched = Expense(
        user_id=user.id,
        amount_cents=100,
        description="Manual",
        category="Outros",
    )
    session.add_all([imported, matched])
    await session.flush()
    session.add_all(
        [
            ExpenseLink(
                user_id=user.id,
                imported_expense_id=imported.id,
                matched_expense_id=matched.id,
            ),
            FinanceSuppression(
                user_id=user.id,
                source="open_finance",
                external_id="transacao-secreta",
            ),
        ]
    )
    await session.flush()
    return connection


@pytest.mark.asyncio
async def test_export_includes_safe_finance_connection_data(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker() as session:
        user = await _user(session)
        await _connection(session, user)

        payload = json.loads(await export_user_data(session, user))

    connection = payload["finance_connections"][0]
    assert set(connection) == {"institution", "status", "created_at", "last_synced_at"}
    assert connection["institution"] == "Banco Exemplo"
    assert connection["status"] == "active"
    assert connection["created_at"] is not None
    assert connection["last_synced_at"] == "2026-11-03T12:00:00+00:00"
    serialized = json.dumps(payload)
    assert "item_ref" not in serialized
    assert "item-secreto-do-agregador" not in serialized
    assert "transacao-secreta" not in serialized
    assert "token" not in serialized


@pytest.mark.asyncio
async def test_deletion_removes_finance_data_after_deleting_remote_items(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    provider = FakeFinanceProvider()
    async with sessionmaker() as session:
        user = await _user(session)
        await _finance_data(session, user)

        await delete_user_data(session, user, provider)
        await session.flush()

        assert provider.deleted_items == ["item-secreto-do-agregador"]
        assert not (await session.scalars(select(FinanceConnection))).all()
        assert not (await session.scalars(select(ExpenseLink))).all()
        assert not (await session.scalars(select(FinanceSuppression))).all()
        assert not (await session.scalars(select(Expense))).all()


class FailingFinanceProvider(FakeFinanceProvider):
    async def delete_item(self, item_ref: str) -> None:
        raise FinanceError


@pytest.mark.asyncio
async def test_deletion_continues_when_finance_provider_fails(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker() as session:
        user = await _user(session)
        await _finance_data(session, user)

        await delete_user_data(session, user, FailingFinanceProvider())
        await session.flush()

        assert not (await session.scalars(select(FinanceConnection))).all()
        assert not (await session.scalars(select(ExpenseLink))).all()
        assert not (await session.scalars(select(FinanceSuppression))).all()
        assert not (await session.scalars(select(Expense))).all()

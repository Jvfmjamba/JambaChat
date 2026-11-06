import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jambachat.conversation.commands import handle_command
from jambachat.expenses.models import Expense
from jambachat.finance.importer import FinanceImporter
from jambachat.finance.models import ExpenseLink, FinanceConnection
from jambachat.finance.provider import ItemInfo
from jambachat.finance.service import FinanceConnectionError, FinanceService
from jambachat.tools.registry import Attachment
from jambachat.users.models import User
from jambachat.users.repository import delete_user_data
from tests.fakes import FakeFinanceProvider
from tests.finance.test_aceite import (
    call,
    make_connection,
    make_manual,
    make_user,
    registry_for,
    tx,
)

Maker = async_sessionmaker[AsyncSession]
ITEMS = {
    "item-a": ItemInfo("Banco Azul", "UPDATED"),
    "item-b": ItemInfo("Banco Verde", "UPDATED"),
}


async def two_users(session: AsyncSession) -> tuple[User, User]:
    return await make_user(session, "1"), await make_user(session, "2")


async def test_another_number_cannot_link_the_same_item(sessionmaker: Maker) -> None:
    provider = FakeFinanceProvider(items=ITEMS)
    service = FinanceService(provider)
    async with sessionmaker() as session:
        owner, intruder = await two_users(session)
        await service.connect(session, owner, "item-a")

        try:
            await service.connect(session, intruder, "item-a")
        except FinanceConnectionError:
            pass
        else:
            raise AssertionError

        reply = await handle_command(session, intruder, "/banco item-a", finance_provider=provider)
        assert reply is not None
        assert "Banco Azul" not in reply
        assert await service.list_connections(session, intruder) == []
        assert [c.user_id for c in await service.list_connections(session, owner)] == [owner.id]


async def test_listing_never_shows_other_users_connections(sessionmaker: Maker) -> None:
    provider = FakeFinanceProvider(items=ITEMS)
    registry = registry_for(provider)
    async with sessionmaker() as session:
        first, second = await two_users(session)
        await make_connection(session, first, "item-a", "Banco Azul")
        await make_connection(session, second, "item-b", "Banco Verde")

        tool = await call(registry, session, first, "list_banks")
        command = await handle_command(session, first, "/banco", finance_provider=provider)

        assert [b["institution"] for b in tool["banks"]] == ["Banco Azul"]
        assert command is not None
        assert "Banco Verde" not in command


async def test_cannot_disconnect_another_users_connection(sessionmaker: Maker) -> None:
    provider = FakeFinanceProvider(items=ITEMS)
    registry = registry_for(provider)
    service = FinanceService(provider)
    async with sessionmaker() as session:
        owner, intruder = await two_users(session)
        connection = await make_connection(session, owner, "item-a", "Banco Azul")

        result = await call(
            registry,
            session,
            intruder,
            "disconnect_bank",
            connection_id=str(connection.id),
            confirm=True,
        )
        assert "error" in result
        command = await handle_command(
            session,
            intruder,
            f"/banco remover {connection.id} confirmar",
            finance_provider=provider,
        )
        assert command == "conexão não encontrada"
        assert provider.deleted_items == []
        assert len(await service.list_connections(session, owner)) == 1


async def test_import_now_only_touches_the_callers_connections(sessionmaker: Maker) -> None:
    provider = FakeFinanceProvider(
        items=ITEMS,
        transactions={"item-a": [tx("a-1")], "item-b": [tx("b-1", -700)]},
    )
    registry = registry_for(provider)
    async with sessionmaker() as session:
        first, second = await two_users(session)
        await make_connection(session, first, "item-a", "Banco Azul")
        await make_connection(session, second, "item-b", "Banco Verde")

        assert await call(registry, session, first, "import_now") == {"imported": 1}

        entries = list(await session.scalars(select(Expense)))
        assert [(e.user_id, e.external_id) for e in entries] == [(first.id, "a-1")]
        third = await make_user(session, "3")
        refused = await call(registry, session, third, "import_now")
        assert "error" in refused


async def test_import_never_links_to_another_users_manual_entry(sessionmaker: Maker) -> None:
    provider = FakeFinanceProvider(transactions={"item-a": [tx("a-1", -4500)]})
    async with sessionmaker() as session:
        owner, other = await two_users(session)
        connection = await make_connection(session, owner, "item-a")
        await make_manual(session, other, 4500, datetime(2026, 11, 5, 15, tzinfo=UTC))

        await FinanceImporter(provider).run(
            session, owner, connection, datetime(2026, 11, 6, 12, tzinfo=UTC)
        )

        assert (await session.scalars(select(ExpenseLink))).all() == []


async def test_export_and_deletion_do_not_cross_users(sessionmaker: Maker) -> None:
    provider = FakeFinanceProvider(items=ITEMS)
    attachments: list[Attachment] = []
    async with sessionmaker() as session:
        first, second = await two_users(session)
        await make_connection(session, first, "item-a", "Banco Azul")
        other = await make_connection(session, second, "item-b", "Banco Verde")

        await handle_command(
            session,
            first,
            "/exportar-meus-dados",
            attachments=attachments,
            finance_provider=provider,
        )
        payload: dict[str, Any] = json.loads(attachments[0].content)
        assert [c["institution"] for c in payload["finance_connections"]] == ["Banco Azul"]

        await delete_user_data(session, first, provider)
        await session.flush()

        assert provider.deleted_items == ["item-a"]
        remaining = list(await session.scalars(select(FinanceConnection)))
        assert [c.id for c in remaining] == [other.id]

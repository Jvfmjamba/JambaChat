import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jambachat.finance.importer import FinanceImporter
from jambachat.finance.errors import FinanceError
from jambachat.finance.provider import ItemInfo
from jambachat.finance.service import FinanceService
from jambachat.finance.tools import build_finance_tools
from jambachat.llm.types import ToolCall
from jambachat.tools.registry import ToolContext, ToolRegistry
from jambachat.users.models import User
from tests.fakes import FakeFinanceProvider, make_settings


class FailingDeleteFinanceProvider(FakeFinanceProvider):
    async def delete_item(self, item_ref: str) -> None:
        raise FinanceError


async def _user(session: AsyncSession, phone: str = "1") -> User:
    user = User(phone=phone, phone_hash=phone)
    session.add(user)
    await session.flush()
    return user


async def _run(
    registry: ToolRegistry, session: AsyncSession, user: User, name: str, **arguments: Any
) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(
        await registry.execute(
            ToolCall("call", name, arguments),
            ToolContext(session, user, make_settings(), datetime(2026, 11, 6, tzinfo=UTC)),
        )
    )
    return result


async def test_finance_tools_hide_item_ref_and_require_disconnect_confirmation(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    provider = FakeFinanceProvider(items={"item-1": ItemInfo("Banco Azul", "UPDATED")})
    registry = ToolRegistry(
        build_finance_tools(provider, FinanceService(provider), FinanceImporter(provider))
    )
    async with sessionmaker() as session:
        user = await _user(session)
        connected = await _run(registry, session, user, "connect_bank", item_ref="item-1")
        listed = await _run(registry, session, user, "list_banks")

        assert connected == {"connected": "Banco Azul", "status": "ativa"}
        assert "item-1" not in json.dumps(listed)
        connection_id = listed["banks"][0]["id"]

        pending = await _run(
            registry, session, user, "disconnect_bank", connection_id=connection_id
        )
        assert "confirmation_required" in pending
        assert provider.deleted_items == []

        disconnected = await _run(
            registry, session, user, "disconnect_bank", connection_id=connection_id, confirm=True
        )
        assert disconnected == {"disconnected": "Banco Azul"}
        assert provider.deleted_items == ["item-1"]


async def test_import_now_respects_manual_sync_interval(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    provider = FakeFinanceProvider(items={"item-1": ItemInfo("Banco Azul", "UPDATED")})
    registry = ToolRegistry(
        build_finance_tools(provider, FinanceService(provider), FinanceImporter(provider))
    )
    async with sessionmaker() as session:
        user = await _user(session)
        await _run(registry, session, user, "connect_bank", item_ref="item-1")

        assert await _run(registry, session, user, "import_now") == {"imported": 0}
        limited = await _run(registry, session, user, "import_now")
        assert "aguarde" in limited["error"]


async def test_disconnect_bank_warns_when_aggregator_deletion_fails(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    provider = FailingDeleteFinanceProvider(items={"item-1": ItemInfo("Banco Azul", "UPDATED")})
    registry = ToolRegistry(
        build_finance_tools(provider, FinanceService(provider), FinanceImporter(provider))
    )
    async with sessionmaker() as session:
        user = await _user(session)
        await _run(registry, session, user, "connect_bank", item_ref="item-1")
        listed = await _run(registry, session, user, "list_banks")

        result = await _run(
            registry,
            session,
            user,
            "disconnect_bank",
            connection_id=listed["banks"][0]["id"],
            confirm=True,
        )
        assert result == {
            "disconnected": "Banco Azul",
            "warning": "A remoção no agregador falhou.",
        }

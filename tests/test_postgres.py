import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from jambachat.config import get_settings
from jambachat.db.models import Base
from jambachat.llm.types import ToolCall
from jambachat.reminders.models import Reminder, ReminderStatus
from jambachat.reminders.scheduler import ReminderDispatcher
from jambachat.tools.catalog import build_registry
from jambachat.tools.registry import ToolContext
from jambachat.users.models import User
from jambachat.whatsapp.client import WhatsAppClient
from tests.fakes import GRAPH_MESSAGES_URL, graph_ok, make_settings
from tests.test_migrations import ROOT

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")

pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL não definida")


@pytest.fixture(scope="module")
def migrated() -> Iterator[None]:
    os.environ["DATABASE_URL"] = POSTGRES_URL
    get_settings.cache_clear()
    config = Config(str(ROOT / "alembic.ini"))
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    yield
    command.downgrade(config, "base")
    os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
    get_settings.cache_clear()


async def test_dispatcher_with_skip_locked(migrated: None) -> None:
    engine = create_async_engine(POSTGRES_URL)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    async with sessionmaker() as session:
        await session.execute(text("DELETE FROM reminders"))
        await session.execute(text("DELETE FROM messages"))
        await session.execute(text("DELETE FROM users"))
        user = User(phone="5511", phone_hash="pg", last_inbound_at=now)
        session.add(user)
        await session.flush()
        session.add(Reminder(user_id=user.id, text="x", due_at=now - timedelta(seconds=1)))
        await session.commit()

    with respx.mock:
        respx.post(GRAPH_MESSAGES_URL).mock(side_effect=graph_ok)
        async with httpx.AsyncClient() as http:
            whatsapp = WhatsAppClient(
                http, access_token="t", phone_number_id="123", api_version="v23.0"
            )
            dispatcher = ReminderDispatcher(sessionmaker, whatsapp, make_settings())
            assert await dispatcher.run_once(now) == 1
            assert await dispatcher.run_once(now) == 0

    async with sessionmaker() as session:
        status = await session.scalar(select(Reminder.status))
    assert status == ReminderStatus.SENT
    await engine.dispose()


async def test_models_match_migrations_on_postgres(migrated: None) -> None:
    engine = create_async_engine(POSTGRES_URL)
    async with engine.connect() as connection:
        diff = await connection.run_sync(
            lambda sync: compare_metadata(MigrationContext.configure(sync), Base.metadata)
        )
    await engine.dispose()
    assert diff == []


async def test_finance_tools_on_postgres(migrated: None) -> None:
    engine = create_async_engine(POSTGRES_URL)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    registry = build_registry()
    async with sessionmaker() as session:
        user = User(phone="5512", phone_hash="pg-finance", timezone="America/Sao_Paulo")
        session.add(user)
        await session.flush()
        ctx = ToolContext(session, user, make_settings(), datetime.now(ZoneInfo(user.timezone)))

        async def run(name: str, **args: object) -> dict[str, object]:
            result: dict[str, object] = json.loads(
                await registry.execute(ToolCall("c", name, args), ctx)
            )
            assert "error" not in result, result
            return result

        await run("set_budget", category="Mercado", amount="100")
        added = await run("add_entry", amount="120,50", description="feira", category="Mercado")
        summary = await run("summarize_entries")
        await session.rollback()
    await engine.dispose()

    assert added["budgets"][0]["exceeded_by"] == "R$ 20,50"  # type: ignore[index]
    assert summary["expenses"] == "R$ 120,50"

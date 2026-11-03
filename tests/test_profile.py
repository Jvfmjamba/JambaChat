import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jambachat.conversation.prompt import build_system_prompt
from jambachat.llm.types import ToolCall
from jambachat.tools.catalog import build_registry
from jambachat.tools.registry import ToolContext
from jambachat.users.models import User
from tests.fakes import make_settings


@pytest.fixture
async def ctx(sessionmaker: async_sessionmaker[AsyncSession]) -> AsyncIterator[ToolContext]:
    async with sessionmaker() as session:
        user = User(phone="1", phone_hash="1", timezone="America/Sao_Paulo", name="João")
        session.add(user)
        await session.flush()
        yield ToolContext(session, user, make_settings(), datetime(2026, 11, 3, 15, tzinfo=UTC))


async def run(ctx: ToolContext, tool: str, **args: Any) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(
        await build_registry().execute(ToolCall("c", tool, args), ctx)
    )
    return result


async def test_update_profile(ctx: ToolContext) -> None:
    result = await run(ctx, "update_profile", name="Jão", timezone="manaus", home_city="Manaus, AM")

    assert result["updated"]["name"] == "Jão"
    assert result["updated"]["timezone"] == "America/Manaus"
    assert result["updated"]["local_time"] == "ter, 03/11/2026 às 11:00"
    assert ctx.user.home_city == "Manaus, AM"

    await run(ctx, "update_profile", home_city="", timezone="europe/lisbon")
    profile = await run(ctx, "get_profile")
    assert profile["home_city"] is None
    assert profile["timezone"] == "Europe/Lisbon"


async def test_update_profile_rejects_bad_input(ctx: ToolContext) -> None:
    assert "desconhecido" in (await run(ctx, "update_profile", timezone="Marte/Base"))["error"]
    assert "nada para alterar" in (await run(ctx, "update_profile"))["error"]
    assert ctx.user.timezone == "America/Sao_Paulo"


def test_prompt_mentions_home_city() -> None:
    now = datetime(2026, 11, 3, 12, tzinfo=UTC)
    assert "Cidade do usuário: Recife." in build_system_prompt(now, "UTC", None, "Recife")
    assert "Cidade do usuário" not in build_system_prompt(now, "UTC", None)

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jambachat.lists.tools import list_key
from jambachat.llm.types import ToolCall
from jambachat.tools.catalog import build_registry
from jambachat.tools.registry import ToolContext
from jambachat.users.models import User
from tests.fakes import make_settings


@pytest.fixture
async def session(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with sessionmaker() as s:
        yield s


async def new_ctx(session: AsyncSession, phone: str) -> ToolContext:
    user = User(phone=phone, phone_hash=phone)
    session.add(user)
    await session.flush()
    return ToolContext(session, user, make_settings(), datetime(2026, 11, 3, tzinfo=UTC))


async def run(ctx: ToolContext, tool: str, **args: Any) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(
        await build_registry().execute(ToolCall("c", tool, args), ctx)
    )
    return result


def test_list_key() -> None:
    assert list_key("Lista de Compras") == list_key("compras") == "compras"
    assert list_key("  Lista  do  Mês ") == "mes"


async def test_add_show_and_check(session: AsyncSession) -> None:
    ctx = await new_ctx(session, "1")
    added = await run(ctx, "list_add", list_name="Compras", items=["leite", "Pão", "café"])
    assert [i["text"] for i in added["items"]] == ["leite", "Pão", "café"]

    again = await run(ctx, "list_add", list_name="lista de compras", items=["pao", "ovos"])
    assert again["already_there"] == ["pao"]
    assert again["list"] == "Compras"
    assert again["pending"] == 4

    checked = await run(
        ctx, "list_update", list_name="compras", action="marcar", items=["1", "cafe"]
    )
    assert [i["done"] for i in checked["items"]] == [True, False, True, False]
    assert checked["pending"] == 2

    readded = await run(ctx, "list_add", list_name="compras", items=["leite"])
    assert readded["items"][0] == {"number": 1, "text": "leite", "done": False}

    cleaned = await run(ctx, "list_update", list_name="compras", action="limpar_marcados")
    assert cleaned["removed"] == 1
    assert [i["text"] for i in cleaned["items"]] == ["leite", "Pão", "ovos"]

    await run(ctx, "list_add", list_name="Filmes", items=["Duna"])
    overview = await run(ctx, "list_show")
    assert overview["lists"] == [
        {"list": "Compras", "items": 3, "pending": 3},
        {"list": "Filmes", "items": 1, "pending": 1},
    ]


async def test_remove_and_delete_list(session: AsyncSession) -> None:
    ctx = await new_ctx(session, "1")
    await run(ctx, "list_add", list_name="tarefas", items=["lavar carro", "lavar louça", "pagar"])

    ambiguous = await run(
        ctx, "list_update", list_name="tarefas", action="remover", items=["lavar"]
    )
    assert "use o número" in ambiguous["error"]
    missing = await run(ctx, "list_update", list_name="tarefas", action="remover", items=["9"])
    assert "número 9" in missing["error"]

    removed = await run(ctx, "list_update", list_name="tarefas", action="remover", items=["pag"])
    assert [i["text"] for i in removed["items"]] == ["lavar carro", "lavar louça"]

    deleted = await run(ctx, "list_update", list_name="tarefas", action="apagar_lista")
    assert deleted == {"deleted_list": "tarefas", "removed": 2}
    assert "não existe" in (await run(ctx, "list_show", list_name="tarefas"))["error"]


async def test_lists_are_private(session: AsyncSession) -> None:
    owner = await new_ctx(session, "1")
    intruder = await new_ctx(session, "2")
    await run(owner, "list_add", list_name="segredos", items=["x"])

    assert (await run(intruder, "list_show"))["lists"] == []
    result = await run(intruder, "list_update", list_name="segredos", action="apagar_lista")
    assert "não existe" in result["error"]

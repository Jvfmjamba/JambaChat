import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jambachat.llm.types import ToolCall
from jambachat.tools.catalog import build_registry
from jambachat.tools.registry import ToolContext
from jambachat.users.models import User
from tests.fakes import make_settings

NOW = datetime(2026, 11, 20, 15, 0, tzinfo=UTC)


@pytest.fixture
async def session(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with sessionmaker() as s:
        yield s


async def new_user(session: AsyncSession, phone: str) -> User:
    user = User(phone=phone, phone_hash=phone, timezone="America/Sao_Paulo")
    session.add(user)
    await session.flush()
    return user


async def run(session: AsyncSession, user: User, name: str, **args: Any) -> dict[str, Any]:
    ctx = ToolContext(
        session=session,
        user=user,
        settings=make_settings(),
        now=NOW.astimezone(ZoneInfo(user.timezone)),
    )
    result: dict[str, Any] = json.loads(
        await build_registry().execute(ToolCall("c", name, args), ctx)
    )
    return result


async def test_add_and_summarize_month(session: AsyncSession) -> None:
    user = await new_user(session, "1")
    added = await run(
        session, user, "add_entry", amount="42,50", description="iFood", category="Alimentação"
    )
    await run(session, user, "add_entry", amount=120, description="mercado", category="Mercado")
    await run(session, user, "add_entry", amount="3.000", description="salário", kind="receita")
    await run(
        session,
        user,
        "add_entry",
        amount=10,
        description="pão",
        category="Alimentação",
        date="2026-10-15",
    )

    assert added["added"]["amount"] == "R$ 42,50"
    assert added["added"]["date"] == "sex, 20/11/2026"
    assert added["month_to_date"]["expenses"] == "R$ 42,50"

    summary = await run(session, user, "summarize_entries")
    assert summary["period"] == {"from": "01/11/2026", "to": "20/11/2026"}
    assert summary["expenses"] == "R$ 162,50"
    assert summary["income"] == "R$ 3.000,00"
    assert summary["balance"] == "R$ 2.837,50"
    assert summary["expenses_by_category"][0] == {
        "category": "Mercado",
        "amount": "R$ 120,00",
        "share": "74%",
    }
    assert summary["previous_period_expenses"] == "R$ 10,00"
    assert summary["daily_average_expenses"] == "R$ 8,12"


async def test_list_filters_and_search(session: AsyncSession) -> None:
    user = await new_user(session, "1")
    await run(session, user, "add_entry", amount=30, description="Uber", category="Transporte")
    await run(session, user, "add_entry", amount=25, description="Café", category="Alimentação")
    await run(session, user, "add_entry", amount=18, description="café da manhã")

    by_text = await run(session, user, "list_entries", search="cafe")
    assert by_text["count"] == 2
    assert by_text["total_expenses"] == "R$ 43,00"

    by_category = await run(session, user, "list_entries", category="Transporte")
    assert [e["description"] for e in by_category["entries"]] == ["Uber"]

    limited = await run(session, user, "list_entries", limit=1)
    assert (limited["count"], limited["shown"], len(limited["entries"])) == (3, 1, 1)


async def test_update_and_delete(session: AsyncSession) -> None:
    user = await new_user(session, "1")
    entry_id = (await run(session, user, "add_entry", amount=50, description="x"))["added"]["id"]

    updated = await run(
        session, user, "update_entry", entry_id=entry_id, amount="55,90", category="Lazer"
    )
    assert updated["updated"]["amount"] == "R$ 55,90"
    assert updated["updated"]["category"] == "Lazer"
    assert updated["updated"]["description"] == "x"

    assert "deleted" in await run(session, user, "delete_entry", entry_id=entry_id)
    assert (await run(session, user, "list_entries"))["count"] == 0
    again = await run(session, user, "delete_entry", entry_id=entry_id)
    assert "não encontrado" in again["error"]


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ({"amount": "0"}, "maior que zero"),
        ({"amount": "-5"}, "maior que zero"),
        ({"amount": "abc"}, "valor inválido"),
        ({"amount": "10", "date": "2026-11-21"}, "futuro"),
        ({"amount": "10", "date": "ontem"}, "data inválida"),
    ],
)
async def test_add_rejects_bad_input(
    session: AsyncSession, args: dict[str, Any], message: str
) -> None:
    user = await new_user(session, "1")
    result = await run(session, user, "add_entry", description="x", **args)

    assert message in result["error"]


async def test_period_validation(session: AsyncSession) -> None:
    user = await new_user(session, "1")
    inverted = await run(
        session, user, "list_entries", start_date="2026-11-10", end_date="2026-11-01"
    )
    assert "anterior ao início" in inverted["error"]
    too_long = await run(
        session, user, "summarize_entries", start_date="2020-01-01", end_date="2026-11-01"
    )
    assert "3 anos" in too_long["error"]


async def test_users_cannot_touch_each_other_entries(session: AsyncSession) -> None:
    owner = await new_user(session, "1")
    intruder = await new_user(session, "2")
    entry_id = (await run(session, owner, "add_entry", amount=50, description="x"))["added"]["id"]

    for name, args in [
        ("update_entry", {"entry_id": entry_id, "amount": "1"}),
        ("delete_entry", {"entry_id": entry_id}),
        ("delete_entry", {"entry_id": "nao-e-uuid"}),
    ]:
        assert "não encontrado" in (await run(session, intruder, name, **args))["error"]
    assert (await run(session, intruder, "list_entries"))["count"] == 0

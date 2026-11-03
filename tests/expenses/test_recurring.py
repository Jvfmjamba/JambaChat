import json
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jambachat.expenses.models import Expense, RecurringEntry
from jambachat.expenses.recurring import (
    RecurringEntryJob,
    first_run,
    next_occurrence,
    occurrence,
)
from jambachat.llm.types import ToolCall
from jambachat.tools.catalog import build_registry
from jambachat.tools.registry import ToolContext
from jambachat.users.models import User
from tests.fakes import make_settings

SP = ZoneInfo("America/Sao_Paulo")
Maker = async_sessionmaker[AsyncSession]


def test_dates() -> None:
    assert occurrence(2026, 2, 31) == date(2026, 2, 28)
    assert next_occurrence(31, date(2026, 1, 31)) == date(2026, 2, 28)
    assert next_occurrence(31, date(2026, 2, 28)) == date(2026, 3, 31)
    assert next_occurrence(5, date(2026, 12, 5)) == date(2027, 1, 5)
    assert first_run(5, date(2026, 11, 3), False) == date(2026, 11, 5)
    assert first_run(3, date(2026, 11, 3), False) == date(2026, 12, 3)
    assert first_run(3, date(2026, 11, 3), True) == date(2026, 11, 3)


async def run(ctx: ToolContext, tool: str, **args: Any) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(
        await build_registry().execute(ToolCall("c", tool, args), ctx)
    )
    return result


async def test_recurring_tools_and_job(sessionmaker: Maker) -> None:
    now = datetime(2026, 11, 3, 15, tzinfo=UTC)
    async with sessionmaker() as session:
        user = User(phone="1", phone_hash="1", timezone="America/Sao_Paulo")
        session.add(user)
        await session.flush()
        ctx = ToolContext(session, user, make_settings(), now.astimezone(SP))

        rent = await run(
            ctx,
            "add_recurring_entry",
            amount="1.500",
            description="aluguel",
            category="Moradia",
            day_of_month=5,
        )
        salary = await run(
            ctx,
            "add_recurring_entry",
            amount="5000",
            description="salário",
            category="Salário",
            kind="receita",
            day_of_month=3,
            include_today=True,
        )
        assert rent["created"]["next"] == "05/11/2026"
        assert salary["created"]["next"] == "03/11/2026"

        listed = await run(ctx, "list_recurring_entries")
        assert listed["monthly_expenses"] == "R$ 1.500,00"
        assert listed["monthly_income"] == "R$ 5.000,00"
        await session.commit()

    job = RecurringEntryJob(sessionmaker)
    assert await job.run_once(now) == 1
    assert await job.run_once(now) == 0
    assert await job.run_once(datetime(2027, 1, 6, 12, tzinfo=UTC)) == 5

    async with sessionmaker() as session:
        entries = list(await session.scalars(select(Expense).order_by(Expense.occurred_at)))
        rules = {r.description: r for r in await session.scalars(select(RecurringEntry))}
    assert [(e.description, e.occurred_at.astimezone(SP).date()) for e in entries] == [
        ("salário", date(2026, 11, 3)),
        ("aluguel", date(2026, 11, 5)),
        ("salário", date(2026, 12, 3)),
        ("aluguel", date(2026, 12, 5)),
        ("salário", date(2027, 1, 3)),
        ("aluguel", date(2027, 1, 5)),
    ]
    assert rules["aluguel"].next_run_on == date(2027, 2, 5)


async def test_cancel_recurring_is_private(sessionmaker: Maker) -> None:
    async with sessionmaker() as session:
        owner = User(phone="1", phone_hash="1")
        intruder = User(phone="2", phone_hash="2")
        session.add_all([owner, intruder])
        await session.flush()
        now = datetime(2026, 11, 3, tzinfo=UTC)
        owner_ctx = ToolContext(session, owner, make_settings(), now)
        intruder_ctx = ToolContext(session, intruder, make_settings(), now)

        created = await run(
            owner_ctx,
            "add_recurring_entry",
            amount="39,90",
            description="Netflix",
            category="Assinaturas",
            day_of_month=10,
        )
        rule_id = created["created"]["id"]

        denied = await run(intruder_ctx, "cancel_recurring_entry", recurring_id=rule_id)
        assert "não encontrada" in denied["error"]
        assert "cancelled" in await run(owner_ctx, "cancel_recurring_entry", recurring_id=rule_id)
        assert (await run(owner_ctx, "list_recurring_entries"))["count"] == 0


@pytest.mark.parametrize("amount", ["0", "abc"])
async def test_add_recurring_rejects_bad_amount(sessionmaker: Maker, amount: str) -> None:
    async with sessionmaker() as session:
        user = User(phone="1", phone_hash="1")
        session.add(user)
        await session.flush()
        ctx = ToolContext(session, user, make_settings(), datetime(2026, 11, 3, tzinfo=UTC))
        result = await run(
            ctx, "add_recurring_entry", amount=amount, description="x", day_of_month=1
        )
    assert "error" in result


async def test_bad_rule_does_not_block_others(sessionmaker: Maker) -> None:
    async with sessionmaker() as session:
        broken = User(phone="1", phone_hash="1", timezone="Fuso/Inexistente")
        fine = User(phone="2", phone_hash="2", timezone="America/Sao_Paulo")
        session.add_all([broken, fine])
        await session.flush()
        for user, day in ((broken, date(2026, 1, 1)), (fine, date(2026, 1, 2))):
            session.add(
                RecurringEntry(
                    user_id=user.id,
                    amount_cents=100,
                    description="x",
                    day_of_month=day.day,
                    next_run_on=day,
                )
            )
        await session.commit()

    assert await RecurringEntryJob(sessionmaker).run_once(datetime(2026, 1, 3, tzinfo=UTC)) == 1

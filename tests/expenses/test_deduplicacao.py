from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jambachat.expenses.budgets import budget_alerts
from jambachat.expenses.models import Budget, EntryKind, EntrySource, Expense
from jambachat.expenses.service import (
    EntryFilter,
    add_entry,
    delete_entry,
    find_entries,
    find_match_for_import,
    summarize,
)
from jambachat.finance.models import ExpenseLink, FinanceSuppression
from jambachat.users.models import User


@pytest.fixture
async def session(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with sessionmaker() as s:
        yield s


async def _user(session: AsyncSession) -> User:
    user = User(phone="1", phone_hash="1", timezone="America/Sao_Paulo")
    session.add(user)
    await session.flush()
    return user


async def _entry(
    session: AsyncSession,
    user: User,
    *,
    source: EntrySource = EntrySource.MANUAL,
    occurred_at: datetime,
    amount_cents: int = 1000,
) -> Expense:
    return await add_entry(
        session,
        user,
        kind=EntryKind.EXPENSE,
        amount_cents=amount_cents,
        description=source.value,
        category="Outros",
        occurred_at=occurred_at,
        source=source,
        external_id="external" if source == EntrySource.OPEN_FINANCE else None,
    )


async def test_find_match_for_import_chooses_closest_then_oldest(session: AsyncSession) -> None:
    user = await _user(session)
    when = datetime(2026, 11, 20, 12, tzinfo=UTC)
    await _entry(session, user, occurred_at=when - timedelta(days=2))
    await _entry(session, user, source=EntrySource.WALLET, occurred_at=when - timedelta(days=1))
    same_day_first = await _entry(session, user, occurred_at=when)
    await _entry(session, user, occurred_at=when)

    assert (
        await find_match_for_import(session, user, EntryKind.EXPENSE, 1000, when)
    ) == same_day_first


async def test_find_match_for_import_excludes_linked_and_other_sources(
    session: AsyncSession,
) -> None:
    user = await _user(session)
    when = datetime(2026, 11, 20, 12, tzinfo=UTC)
    manual = await _entry(session, user, occurred_at=when)
    imported = await _entry(session, user, source=EntrySource.OPEN_FINANCE, occurred_at=when)
    session.add(
        ExpenseLink(user_id=user.id, imported_expense_id=imported.id, matched_expense_id=manual.id)
    )
    await session.flush()

    assert await find_match_for_import(session, user, EntryKind.EXPENSE, 1000, when) is None


async def test_hidden_imports_do_not_count_and_can_be_requested(session: AsyncSession) -> None:
    user = await _user(session)
    when = datetime(2026, 11, 20, 12, tzinfo=UTC)
    manual = await _entry(session, user, occurred_at=when)
    imported = await _entry(session, user, source=EntrySource.OPEN_FINANCE, occurred_at=when)
    session.add(
        ExpenseLink(user_id=user.id, imported_expense_id=imported.id, matched_expense_id=manual.id)
    )
    session.add(Budget(user_id=user.id, category="Outros", amount_cents=2000))
    await session.flush()
    flt = EntryFilter(start=when - timedelta(days=1), end=when + timedelta(days=1))

    assert summarize(await find_entries(session, user, flt)).expense_cents == 1000
    all_entries = await find_entries(
        session,
        user,
        EntryFilter(start=flt.start, end=flt.end, exclude_hidden=False),
    )
    assert len(all_entries) == 2
    assert (await budget_alerts(session, user, when, "Outros"))[0]["spent_this_month"] == "R$ 10,00"


async def test_delete_open_finance_entry_creates_suppression(session: AsyncSession) -> None:
    user = await _user(session)
    entry = await _entry(
        session,
        user,
        source=EntrySource.OPEN_FINANCE,
        occurred_at=datetime(2026, 11, 20, tzinfo=UTC),
    )

    await delete_entry(session, user, str(entry.id))

    suppression = await session.scalar(select(FinanceSuppression))
    assert suppression is not None
    assert suppression.user_id == user.id
    assert suppression.source == EntrySource.OPEN_FINANCE
    assert suppression.external_id == "external"

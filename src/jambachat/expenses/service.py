import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jambachat.expenses.models import EntryKind, EntrySource, Expense
from jambachat.finance.models import ExpenseLink, FinanceSuppression
from jambachat.text import fold
from jambachat.users.models import User


class EntryNotFoundError(Exception):
    pass


@dataclass(frozen=True)
class EntryFilter:
    start: datetime
    end: datetime
    kind: EntryKind | None = None
    category: str | None = None
    query: str | None = None
    exclude_hidden: bool = True


@dataclass
class Summary:
    expense_cents: int = 0
    income_cents: int = 0
    count: int = 0
    by_category: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    @property
    def balance_cents(self) -> int:
        return self.income_cents - self.expense_cents


async def add_entry(
    session: AsyncSession,
    user: User,
    *,
    kind: EntryKind,
    amount_cents: int,
    description: str,
    category: str,
    occurred_at: datetime,
    source: EntrySource = EntrySource.MANUAL,
    external_id: str | None = None,
) -> Expense:
    entry = Expense(
        user_id=user.id,
        kind=kind,
        amount_cents=amount_cents,
        description=description,
        category=category,
        occurred_at=occurred_at,
        source=source,
        external_id=external_id,
    )
    session.add(entry)
    await session.flush()
    return entry


async def find_entries(session: AsyncSession, user: User, flt: EntryFilter) -> list[Expense]:
    query = select(Expense).where(
        Expense.user_id == user.id,
        Expense.occurred_at >= flt.start,
        Expense.occurred_at < flt.end,
    )
    if flt.kind is not None:
        query = query.where(Expense.kind == flt.kind)
    if flt.category is not None:
        query = query.where(Expense.category == flt.category)
    if flt.exclude_hidden:
        query = query.where(
            Expense.id.not_in(
                select(ExpenseLink.imported_expense_id).where(ExpenseLink.user_id == user.id)
            )
        )
    rows = list(await session.scalars(query.order_by(Expense.occurred_at.desc())))
    if flt.query:
        needle = fold(flt.query)
        rows = [r for r in rows if needle in fold(r.description)]
    return rows


async def find_match_for_import(
    session: AsyncSession,
    user: User,
    kind: EntryKind,
    amount_cents: int,
    occurred_at: datetime,
) -> Expense | None:
    candidates = list(
        await session.scalars(
            select(Expense)
            .where(
                Expense.user_id == user.id,
                Expense.kind == kind,
                Expense.amount_cents == amount_cents,
                Expense.source.in_([EntrySource.MANUAL, EntrySource.WALLET]),
                Expense.occurred_at >= occurred_at - timedelta(days=4),
                Expense.occurred_at <= occurred_at + timedelta(days=4),
                Expense.id.not_in(
                    select(ExpenseLink.matched_expense_id).where(ExpenseLink.user_id == user.id)
                ),
            )
            .order_by(Expense.created_at, Expense.id)
        )
    )
    candidates = [
        entry
        for entry in candidates
        if abs((entry.occurred_at.date() - occurred_at.date()).days) <= 3
    ]
    if not candidates:
        return None
    return min(
        candidates, key=lambda entry: abs((entry.occurred_at.date() - occurred_at.date()).days)
    )


def summarize(entries: list[Expense]) -> Summary:
    summary = Summary()
    for entry in entries:
        summary.count += 1
        if entry.kind == EntryKind.INCOME:
            summary.income_cents += entry.amount_cents
        else:
            summary.expense_cents += entry.amount_cents
            summary.by_category[entry.category] += entry.amount_cents
    return summary


async def get_owned(session: AsyncSession, user: User, entry_id: str) -> Expense:
    try:
        key = uuid.UUID(entry_id)
    except ValueError as exc:
        raise EntryNotFoundError from exc
    entry = await session.get(Expense, key)
    if entry is None or entry.user_id != user.id:
        raise EntryNotFoundError
    return entry


async def delete_entry(session: AsyncSession, user: User, entry_id: str) -> Expense:
    entry = await get_owned(session, user, entry_id)
    if entry.source == EntrySource.OPEN_FINANCE and entry.external_id is not None:
        session.add(
            FinanceSuppression(
                user_id=user.id,
                source=entry.source,
                external_id=entry.external_id,
            )
        )
    await session.delete(entry)
    await session.flush()
    return entry

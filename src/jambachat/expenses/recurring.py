import calendar
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

import structlog
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jambachat.expenses.models import Category, EntryKind, Expense, RecurringEntry
from jambachat.money import format_brl, parse_amount
from jambachat.tools.registry import Tool, ToolContext, ToolError
from jambachat.users.models import User

logger = structlog.get_logger(__name__)

MAX_RULES = 50
MAX_CATCH_UP = 12
KIND_MAP = {"gasto": EntryKind.EXPENSE, "receita": EntryKind.INCOME}
KIND_LABEL = {EntryKind.EXPENSE: "gasto", EntryKind.INCOME: "receita"}
NOT_FOUND = "recorrência não encontrada; liste as recorrências para obter o id correto"


def occurrence(year: int, month: int, day: int) -> date:
    """Dia do mês ajustado para meses mais curtos (31 vira 30, 29 ou 28)."""
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def next_occurrence(day: int, after: date) -> date:
    """Próxima data com o dia `day` estritamente depois de `after`."""
    current = occurrence(after.year, after.month, day)
    if current > after:
        return current
    year, month = (after.year + 1, 1) if after.month == 12 else (after.year, after.month + 1)
    return occurrence(year, month, day)


def first_run(day: int, today: date, include_today: bool) -> date:
    this_month = occurrence(today.year, today.month, day)
    if this_month > today or (this_month == today and include_today):
        return this_month
    return next_occurrence(day, today)


class AddRecurringArgs(BaseModel):
    amount: str = Field(description='Valor em reais, ex.: "1500" ou "39,90"')
    description: str = Field(min_length=1, max_length=200, description="Ex.: aluguel, Netflix")
    category: Category = Field(default=Category.OUTROS)
    kind: Literal["gasto", "receita"] = Field(default="gasto")
    day_of_month: int = Field(ge=1, le=31, description="Dia do mês em que o lançamento acontece")
    include_today: bool = Field(
        default=False,
        description="true se o dia é hoje e o lançamento de hoje ainda não foi registrado",
    )


class ListRecurringArgs(BaseModel):
    pass


class CancelRecurringArgs(BaseModel):
    recurring_id: str = Field(description="Id obtido em list_recurring_entries")


def _view(rule: RecurringEntry) -> dict[str, Any]:
    return {
        "id": str(rule.id),
        "kind": KIND_LABEL[EntryKind(rule.kind)],
        "amount": format_brl(rule.amount_cents),
        "description": rule.description,
        "category": rule.category,
        "day_of_month": rule.day_of_month,
        "next": f"{rule.next_run_on:%d/%m/%Y}",
    }


async def _rules(ctx: ToolContext) -> list[RecurringEntry]:
    rows = await ctx.session.scalars(
        select(RecurringEntry)
        .where(RecurringEntry.user_id == ctx.user.id, RecurringEntry.active.is_(True))
        .order_by(RecurringEntry.day_of_month)
    )
    return list(rows)


async def add_recurring(ctx: ToolContext, args: AddRecurringArgs) -> dict[str, Any]:
    try:
        cents = parse_amount(args.amount)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    if len(await _rules(ctx)) >= MAX_RULES:
        raise ToolError(f"limite de {MAX_RULES} recorrências atingido")
    rule = RecurringEntry(
        user_id=ctx.user.id,
        kind=KIND_MAP[args.kind],
        amount_cents=cents,
        description=args.description.strip(),
        category=args.category.value,
        day_of_month=args.day_of_month,
        next_run_on=first_run(args.day_of_month, ctx.now.date(), args.include_today),
    )
    ctx.session.add(rule)
    await ctx.session.flush()
    return {"created": _view(rule), "note": "o lançamento será registrado automaticamente"}


async def list_recurring(ctx: ToolContext, args: ListRecurringArgs) -> dict[str, Any]:
    rules = await _rules(ctx)
    expenses = sum(r.amount_cents for r in rules if r.kind == EntryKind.EXPENSE)
    income = sum(r.amount_cents for r in rules if r.kind == EntryKind.INCOME)
    return {
        "count": len(rules),
        "monthly_expenses": format_brl(expenses),
        "monthly_income": format_brl(income),
        "recurring": [_view(r) for r in rules],
    }


async def cancel_recurring(ctx: ToolContext, args: CancelRecurringArgs) -> dict[str, Any]:
    for rule in await _rules(ctx):
        if str(rule.id) == args.recurring_id.strip():
            rule.active = False
            await ctx.session.flush()
            return {"cancelled": _view(rule)}
    raise ToolError(NOT_FOUND)


RECURRING_TOOLS = [
    Tool(
        "add_recurring_entry",
        "Cria um gasto ou receita que se repete todo mês (aluguel, assinaturas, salário). "
        "O lançamento é registrado sozinho no dia.",
        AddRecurringArgs,
        add_recurring,
    ),
    Tool(
        "list_recurring_entries",
        "Lista os gastos e receitas mensais recorrentes e o total por mês.",
        ListRecurringArgs,
        list_recurring,
    ),
    Tool(
        "cancel_recurring_entry",
        "Encerra um lançamento recorrente (os já registrados continuam).",
        CancelRecurringArgs,
        cancel_recurring,
    ),
]


class RecurringEntryJob:
    """Registra os lançamentos recorrentes que venceram, no fuso de cada usuário."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def run_once(self, now: datetime | None = None) -> int:
        now = now or datetime.now(UTC)
        horizon = (now + timedelta(days=1)).date()
        created = 0
        async with self._sessionmaker() as session:
            rules = list(
                await session.scalars(
                    select(RecurringEntry)
                    .where(RecurringEntry.active.is_(True), RecurringEntry.next_run_on <= horizon)
                    .order_by(RecurringEntry.next_run_on)
                    .limit(200)
                    .with_for_update(skip_locked=True)
                )
            )
            for rule in rules:
                created += await self._apply(session, rule, now)
            await session.commit()
        return created

    async def _apply(self, session: AsyncSession, rule: RecurringEntry, now: datetime) -> int:
        user = await session.get(User, rule.user_id)
        if user is None:
            rule.active = False
            return 0
        tz = ZoneInfo(user.timezone)
        today = now.astimezone(tz).date()
        created = 0
        while rule.next_run_on <= today and created < MAX_CATCH_UP:
            session.add(
                Expense(
                    user_id=rule.user_id,
                    kind=rule.kind,
                    amount_cents=rule.amount_cents,
                    description=rule.description,
                    category=rule.category,
                    occurred_at=datetime.combine(rule.next_run_on, time(12), tz),
                )
            )
            rule.next_run_on = next_occurrence(rule.day_of_month, rule.next_run_on)
            created += 1
        if created:
            logger.info("recurring_entries_created", rule_id=str(rule.id), count=created)
        return created

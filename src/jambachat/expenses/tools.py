from datetime import date, datetime, timedelta
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, BeforeValidator, Field

from jambachat.expenses.budgets import budget_alerts
from jambachat.expenses.models import Category, EntryKind, Expense
from jambachat.expenses.service import (
    EntryFilter,
    EntryNotFoundError,
    add_entry,
    delete_entry,
    find_entries,
    get_owned,
    summarize,
)
from jambachat.money import format_brl, parse_amount
from jambachat.timeutils import (
    format_local_date,
    local_range,
    month_start,
    parse_local_date,
    parse_local_datetime,
)
from jambachat.tools.registry import Tool, ToolContext, ToolError

NOT_FOUND = "lançamento não encontrado; liste os lançamentos para obter o id correto"
MAX_PAST = timedelta(days=10 * 366)
MAX_RANGE_DAYS = 3 * 366
Kind = Literal["gasto", "receita"]
KIND_MAP = {"gasto": EntryKind.EXPENSE, "receita": EntryKind.INCOME}
KIND_LABEL = {EntryKind.EXPENSE: "gasto", EntryKind.INCOME: "receita"}

AMOUNT_HELP = 'Valor em reais, ex.: "42.50" ou "42,50"'
Amount = Annotated[str, BeforeValidator(lambda v: v if isinstance(v, str) else str(v))]
DATE_HELP = "Data em ISO 8601 (AAAA-MM-DD). Sem data, usa hoje."


class AddEntryArgs(BaseModel):
    amount: Amount = Field(description=AMOUNT_HELP)
    description: str = Field(
        min_length=1, max_length=200, description="Onde ou com o quê, ex.: iFood, mercado"
    )
    category: Category = Field(default=Category.OUTROS, description="Categoria mais adequada")
    kind: Kind = Field(default="gasto", description="gasto (saída) ou receita (entrada)")
    date: str | None = Field(default=None, description=DATE_HELP)


class PeriodArgs(BaseModel):
    start_date: str | None = Field(
        default=None, description="Início do período (AAAA-MM-DD). Padrão: dia 1 do mês atual"
    )
    end_date: str | None = Field(
        default=None, description="Fim do período, inclusive. Padrão: hoje"
    )


class ListEntriesArgs(PeriodArgs):
    kind: Kind | None = Field(default=None, description="Filtrar só gastos ou só receitas")
    category: Category | None = Field(default=None, description="Filtrar por categoria")
    search: str | None = Field(
        default=None, max_length=100, description="Texto contido na descrição"
    )
    limit: int = Field(default=20, ge=1, le=50, description="Máximo de itens retornados")


class SummaryArgs(PeriodArgs):
    pass


class UpdateEntryArgs(BaseModel):
    entry_id: str = Field(description="Id do lançamento, obtido em list_entries")
    amount: Amount | None = Field(default=None, description=AMOUNT_HELP)
    description: str | None = Field(default=None, min_length=1, max_length=200)
    category: Category | None = None
    kind: Kind | None = None
    date: str | None = Field(default=None, description="Nova data (AAAA-MM-DD)")


class DeleteEntryArgs(BaseModel):
    entry_id: str = Field(description="Id do lançamento, obtido em list_entries")


def _view(entry: Expense, tz: ZoneInfo) -> dict[str, Any]:
    return {
        "id": str(entry.id),
        "kind": KIND_LABEL[EntryKind(entry.kind)],
        "amount": format_brl(entry.amount_cents),
        "description": entry.description,
        "category": entry.category,
        "date": format_local_date(entry.occurred_at, tz),
    }


def _cents(value: str) -> int:
    try:
        return parse_amount(value)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc


def _occurred_at(value: str | None, ctx: ToolContext) -> datetime:
    if not value:
        return ctx.now
    tz = ZoneInfo(ctx.user.timezone)
    try:
        if "T" in value or " " in value.strip():
            moment = parse_local_datetime(value, tz)
        else:
            day = parse_local_date(value)
            moment = datetime.combine(day, ctx.now.timetz()).replace(tzinfo=tz)
            if day == ctx.now.date():
                moment = ctx.now
    except ValueError as exc:
        raise ToolError("data inválida; use AAAA-MM-DD") from exc
    if moment.astimezone(tz).date() > ctx.now.date():
        raise ToolError("a data está no futuro; registre o lançamento quando acontecer")
    if moment < ctx.now - MAX_PAST:
        raise ToolError("data antiga demais")
    return moment


def _period(args: PeriodArgs, ctx: ToolContext) -> tuple[date, date]:
    today = ctx.now.date()
    try:
        start = parse_local_date(args.start_date) if args.start_date else month_start(today)
        end = parse_local_date(args.end_date) if args.end_date else max(today, start)
    except ValueError as exc:
        raise ToolError("data inválida; use AAAA-MM-DD") from exc
    if end < start:
        raise ToolError("o fim do período é anterior ao início")
    if (end - start).days > MAX_RANGE_DAYS:
        raise ToolError("período longo demais; o limite é de 3 anos")
    return start, end


def _filter(start: date, end: date, tz: ZoneInfo, **extra: Any) -> EntryFilter:
    lower, upper = local_range(start, end, tz)
    return EntryFilter(start=lower, end=upper, **extra)


def _period_view(start: date, end: date) -> dict[str, str]:
    return {"from": f"{start:%d/%m/%Y}", "to": f"{end:%d/%m/%Y}"}


async def add(ctx: ToolContext, args: AddEntryArgs) -> dict[str, Any]:
    entry = await add_entry(
        ctx.session,
        ctx.user,
        kind=KIND_MAP[args.kind],
        amount_cents=_cents(args.amount),
        description=args.description.strip(),
        category=args.category.value,
        occurred_at=_occurred_at(args.date, ctx),
    )
    tz = ZoneInfo(ctx.user.timezone)
    start = month_start(ctx.now.date())
    month = summarize(await find_entries(ctx.session, ctx.user, _filter(start, ctx.now.date(), tz)))
    result: dict[str, Any] = {
        "added": _view(entry, tz),
        "month_to_date": {
            "expenses": format_brl(month.expense_cents),
            "income": format_brl(month.income_cents),
        },
    }
    if entry.kind == EntryKind.EXPENSE:
        alerts = await budget_alerts(ctx.session, ctx.user, ctx.now, entry.category)
        if alerts:
            result["budgets"] = alerts
    return result


async def list_entries(ctx: ToolContext, args: ListEntriesArgs) -> dict[str, Any]:
    tz = ZoneInfo(ctx.user.timezone)
    start, end = _period(args, ctx)
    entries = await find_entries(
        ctx.session,
        ctx.user,
        _filter(
            start,
            end,
            tz,
            kind=KIND_MAP[args.kind] if args.kind else None,
            category=args.category.value if args.category else None,
            query=args.search,
        ),
    )
    totals = summarize(entries)
    return {
        "period": _period_view(start, end),
        "count": len(entries),
        "shown": min(len(entries), args.limit),
        "total_expenses": format_brl(totals.expense_cents),
        "total_income": format_brl(totals.income_cents),
        "entries": [_view(e, tz) for e in entries[: args.limit]],
    }


async def summary(ctx: ToolContext, args: SummaryArgs) -> dict[str, Any]:
    tz = ZoneInfo(ctx.user.timezone)
    start, end = _period(args, ctx)
    current = summarize(await find_entries(ctx.session, ctx.user, _filter(start, end, tz)))
    length = (end - start).days + 1
    prev_end = start - timedelta(days=1)
    previous = summarize(
        await find_entries(
            ctx.session, ctx.user, _filter(prev_end - timedelta(days=length - 1), prev_end, tz)
        )
    )
    ranking = sorted(current.by_category.items(), key=lambda item: item[1], reverse=True)
    result: dict[str, Any] = {
        "period": _period_view(start, end),
        "entries": current.count,
        "expenses": format_brl(current.expense_cents),
        "income": format_brl(current.income_cents),
        "balance": format_brl(current.balance_cents),
        "expenses_by_category": [
            {
                "category": name,
                "amount": format_brl(cents),
                "share": f"{cents * 100 / current.expense_cents:.0f}%",
            }
            for name, cents in ranking
        ],
        "previous_period_expenses": format_brl(previous.expense_cents),
    }
    if length > 1:
        elapsed = min(length, max(1, (ctx.now.date() - start).days + 1))
        result["daily_average_expenses"] = format_brl(current.expense_cents // elapsed)
    return result


async def update(ctx: ToolContext, args: UpdateEntryArgs) -> dict[str, Any]:
    try:
        entry = await get_owned(ctx.session, ctx.user, args.entry_id)
    except EntryNotFoundError as exc:
        raise ToolError(NOT_FOUND) from exc
    if args.amount is not None:
        entry.amount_cents = _cents(args.amount)
    if args.description is not None:
        entry.description = args.description.strip()
    if args.category is not None:
        entry.category = args.category.value
    if args.kind is not None:
        entry.kind = KIND_MAP[args.kind]
    if args.date is not None:
        entry.occurred_at = _occurred_at(args.date, ctx)
    await ctx.session.flush()
    return {"updated": _view(entry, ZoneInfo(ctx.user.timezone))}


async def delete(ctx: ToolContext, args: DeleteEntryArgs) -> dict[str, Any]:
    try:
        entry = await delete_entry(ctx.session, ctx.user, args.entry_id)
    except EntryNotFoundError as exc:
        raise ToolError(NOT_FOUND) from exc
    return {"deleted": _view(entry, ZoneInfo(ctx.user.timezone))}


EXPENSE_TOOLS = [
    Tool(
        "add_entry",
        "Registra um gasto ou uma receita do usuário. Use para frases como "
        '"gastei 42 no ifood" ou "recebi 3000 de salário".',
        AddEntryArgs,
        add,
    ),
    Tool(
        "list_entries",
        "Lista gastos e receitas de um período, com filtros por tipo, categoria e texto.",
        ListEntriesArgs,
        list_entries,
    ),
    Tool(
        "summarize_entries",
        "Resume as finanças de um período: totais, saldo, gastos por categoria e "
        "comparação com o período anterior de mesmo tamanho.",
        SummaryArgs,
        summary,
    ),
    Tool(
        "update_entry",
        "Corrige valor, descrição, categoria, tipo ou data de um lançamento.",
        UpdateEntryArgs,
        update,
    ),
    Tool("delete_entry", "Apaga um lançamento.", DeleteEntryArgs, delete),
]

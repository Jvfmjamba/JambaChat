from datetime import datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jambachat.expenses.models import TOTAL_BUDGET, Budget, Category, EntryKind
from jambachat.expenses.service import EntryFilter, find_entries, summarize
from jambachat.money import format_brl, parse_amount
from jambachat.timeutils import local_range, month_start
from jambachat.tools.registry import Tool, ToolArgs, ToolContext, ToolError
from jambachat.users.models import User

BudgetCategory = Category | Literal["Total"]


class SetBudgetArgs(ToolArgs):
    category: BudgetCategory = Field(
        description='Categoria do orçamento, ou "Total" para o limite geral do mês'
    )
    amount: str = Field(description='Limite mensal em reais, ex.: "800" ou "1.500,00"')


class ListBudgetsArgs(ToolArgs):
    pass


class DeleteBudgetArgs(ToolArgs):
    category: BudgetCategory = Field(description='Categoria do orçamento, ou "Total"')


async def _month_spending(session: AsyncSession, user: User, now: datetime) -> dict[str, int]:
    tz = ZoneInfo(user.timezone)
    lower, upper = local_range(month_start(now.date()), now.date(), tz)
    entries = await find_entries(
        session, user, EntryFilter(start=lower, end=upper, kind=EntryKind.EXPENSE)
    )
    summary = summarize(entries)
    return {**summary.by_category, TOTAL_BUDGET: summary.expense_cents}


def _status(budget: Budget, spent: int) -> dict[str, Any]:
    remaining = budget.amount_cents - spent
    return {
        "category": budget.category,
        "limit": format_brl(budget.amount_cents),
        "spent_this_month": format_brl(spent),
        "remaining": format_brl(max(remaining, 0)),
        "used": f"{spent * 100 / budget.amount_cents:.0f}%",
        "exceeded_by": format_brl(-remaining) if remaining < 0 else None,
    }


async def budget_alerts(
    session: AsyncSession, user: User, now: datetime, category: str
) -> list[dict[str, Any]]:
    """Situação dos orçamentos afetados por um gasto na categoria, se houver orçamento."""
    budgets = list(
        await session.scalars(
            select(Budget).where(
                Budget.user_id == user.id, Budget.category.in_([category, TOTAL_BUDGET])
            )
        )
    )
    if not budgets:
        return []
    spending = await _month_spending(session, user, now)
    return [_status(b, spending.get(b.category, 0)) for b in budgets]


async def set_budget(ctx: ToolContext, args: SetBudgetArgs) -> dict[str, Any]:
    try:
        cents = parse_amount(args.amount)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    category = str(args.category)
    budget = await ctx.session.scalar(
        select(Budget).where(Budget.user_id == ctx.user.id, Budget.category == category)
    )
    if budget is None:
        budget = Budget(user_id=ctx.user.id, category=category, amount_cents=cents)
        ctx.session.add(budget)
    else:
        budget.amount_cents = cents
    await ctx.session.flush()
    spending = await _month_spending(ctx.session, ctx.user, ctx.now)
    return {"budget": _status(budget, spending.get(category, 0))}


async def list_budgets(ctx: ToolContext, args: ListBudgetsArgs) -> dict[str, Any]:
    budgets = list(
        await ctx.session.scalars(
            select(Budget).where(Budget.user_id == ctx.user.id).order_by(Budget.category)
        )
    )
    spending = await _month_spending(ctx.session, ctx.user, ctx.now) if budgets else {}
    return {
        "count": len(budgets),
        "budgets": [_status(b, spending.get(b.category, 0)) for b in budgets],
    }


async def delete_budget(ctx: ToolContext, args: DeleteBudgetArgs) -> dict[str, Any]:
    budget = await ctx.session.scalar(
        select(Budget).where(Budget.user_id == ctx.user.id, Budget.category == str(args.category))
    )
    if budget is None:
        raise ToolError("não há orçamento para essa categoria")
    await ctx.session.delete(budget)
    await ctx.session.flush()
    return {"deleted": budget.category}


BUDGET_TOOLS = [
    Tool(
        "set_budget",
        "Define ou altera o limite mensal de gastos de uma categoria ou o limite total.",
        SetBudgetArgs,
        set_budget,
    ),
    Tool(
        "list_budgets",
        "Lista os orçamentos mensais com quanto já foi gasto e quanto resta no mês.",
        ListBudgetsArgs,
        list_budgets,
    ),
    Tool(
        "delete_budget",
        "Remove o orçamento de uma categoria.",
        DeleteBudgetArgs,
        delete_budget,
        destructive=True,
    ),
]

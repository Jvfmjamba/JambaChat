import json
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jambachat.conversation.models import Message, Role
from jambachat.expenses.models import Budget, Expense, RecurringEntry
from jambachat.lists.models import ListItem
from jambachat.reminders.models import Reminder
from jambachat.timeutils import as_utc
from jambachat.users.models import User


def _iso(value: datetime | None) -> str | None:
    return as_utc(value).isoformat() if value is not None else None


async def export_user_data(session: AsyncSession, user: User) -> bytes:
    """Tudo o que guardamos sobre o usuário, em JSON legível (LGPD, art. 18, V)."""

    async def rows(model: Any) -> list[Any]:
        query = select(model).where(model.user_id == user.id).order_by(model.created_at)
        return list(await session.scalars(query))

    data = {
        "exported_at": _iso(datetime.now().astimezone()),
        "profile": {
            "name": user.name,
            "phone": user.phone,
            "timezone": user.timezone,
            "home_city": user.home_city,
            "created_at": _iso(user.created_at),
        },
        "reminders": [
            {
                "text": r.text,
                "due_at": _iso(r.due_at),
                "recurrence": r.recurrence,
                "status": r.status,
            }
            for r in await rows(Reminder)
        ],
        "entries": [
            {
                "kind": e.kind,
                "amount_cents": e.amount_cents,
                "currency": e.currency,
                "description": e.description,
                "category": e.category,
                "occurred_at": _iso(e.occurred_at),
            }
            for e in await rows(Expense)
        ],
        "budgets": [
            {"category": b.category, "amount_cents": b.amount_cents} for b in await rows(Budget)
        ],
        "recurring_entries": [
            {
                "kind": r.kind,
                "amount_cents": r.amount_cents,
                "description": r.description,
                "category": r.category,
                "day_of_month": r.day_of_month,
                "active": r.active,
            }
            for r in await rows(RecurringEntry)
        ],
        "lists": [
            {"list": i.list_name, "item": i.text, "done": i.done} for i in await rows(ListItem)
        ],
        "messages": [
            {"role": m.role, "text": m.content.get("text"), "at": _iso(m.created_at)}
            for m in await rows(Message)
            if m.role in {Role.USER, Role.ASSISTANT} and m.content.get("text")
        ],
    }
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")

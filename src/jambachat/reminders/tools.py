from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from jambachat.reminders.models import Reminder, ReminderStatus
from jambachat.reminders.recurrence import Recurrence, describe
from jambachat.reminders.service import (
    ReminderNotFoundError,
    cancel_reminder,
    create_reminder,
    get_owned,
    list_pending,
    snooze_reminder,
)
from jambachat.timeutils import format_local, parse_local_datetime
from jambachat.tools.registry import Tool, ToolContext, ToolError

NOT_FOUND = "lembrete não encontrado; liste os lembretes para obter o id correto"
MAX_HORIZON = timedelta(days=5 * 365)
PAST_TOLERANCE = timedelta(minutes=1)


class CreateReminderArgs(BaseModel):
    text: str = Field(min_length=1, max_length=500, description="O que lembrar, curto e claro")
    due_at: str = Field(
        description="Data e hora da primeira ocorrência em ISO 8601, ex.: 2026-11-07T09:00. "
        "Sem fuso, assume o fuso do usuário."
    )
    recurrence: Recurrence | None = Field(default=None, description="Só se o lembrete repetir")


class ListRemindersArgs(BaseModel):
    pass


class CancelReminderArgs(BaseModel):
    reminder_id: str = Field(description="Id do lembrete, obtido em list_reminders")


class UpdateReminderArgs(BaseModel):
    reminder_id: str = Field(description="Id do lembrete, obtido em list_reminders")
    text: str | None = Field(default=None, min_length=1, max_length=500, description="Novo texto")
    due_at: str | None = Field(
        default=None, description="Nova data e hora em ISO 8601, ex.: 2026-11-07T09:00"
    )
    recurrence: Recurrence | None = Field(default=None, description="Nova regra de repetição")
    remove_recurrence: bool = Field(
        default=False, description="true para o lembrete deixar de repetir"
    )


class SnoozeReminderArgs(BaseModel):
    minutes: int = Field(ge=1, le=1440, description="Quantos minutos adiar")
    reminder_id: str | None = Field(
        default=None, description="Id do lembrete; se omitido, adia o último lembrete enviado"
    )


def _view(reminder: Reminder, tz: ZoneInfo) -> dict[str, Any]:
    return {
        "id": str(reminder.id),
        "text": reminder.text,
        "due_at": format_local(reminder.due_at, tz),
        "recurrence": describe(reminder.recurrence) if reminder.recurrence else None,
    }


def _due_at(value: str, ctx: ToolContext) -> datetime:
    try:
        due_at = parse_local_datetime(value, ZoneInfo(ctx.user.timezone))
    except ValueError as exc:
        raise ToolError("data inválida; use ISO 8601, ex.: 2026-11-07T09:00") from exc
    if due_at < ctx.now - PAST_TOLERANCE:
        raise ToolError("a data já passou; confirme a data com o usuário")
    if due_at > ctx.now + MAX_HORIZON:
        raise ToolError("data distante demais; o limite é de 5 anos")
    return due_at


async def create(ctx: ToolContext, args: CreateReminderArgs) -> dict[str, Any]:
    tz = ZoneInfo(ctx.user.timezone)
    due_at = _due_at(args.due_at, ctx)
    rule = args.recurrence.to_rrule() if args.recurrence else None
    reminder = await create_reminder(ctx.session, ctx.user, args.text.strip(), due_at, rule)
    return {"created": _view(reminder, tz)}


async def list_all(ctx: ToolContext, args: ListRemindersArgs) -> dict[str, Any]:
    tz = ZoneInfo(ctx.user.timezone)
    reminders = await list_pending(ctx.session, ctx.user)
    return {"count": len(reminders), "reminders": [_view(r, tz) for r in reminders]}


async def cancel(ctx: ToolContext, args: CancelReminderArgs) -> dict[str, Any]:
    try:
        reminder = await cancel_reminder(ctx.session, ctx.user, args.reminder_id)
    except ReminderNotFoundError as exc:
        raise ToolError(NOT_FOUND) from exc
    return {"cancelled": _view(reminder, ZoneInfo(ctx.user.timezone))}


async def update(ctx: ToolContext, args: UpdateReminderArgs) -> dict[str, Any]:
    try:
        reminder = await get_owned(ctx.session, ctx.user, args.reminder_id)
    except ReminderNotFoundError as exc:
        raise ToolError(NOT_FOUND) from exc
    if reminder.status != ReminderStatus.PENDING:
        raise ToolError(NOT_FOUND)
    if args.text is not None:
        reminder.text = args.text.strip()
    if args.due_at is not None:
        reminder.due_at = _due_at(args.due_at, ctx)
        reminder.attempts = 0
        reminder.next_attempt_at = None
    if args.remove_recurrence:
        reminder.recurrence = None
    elif args.recurrence is not None:
        reminder.recurrence = args.recurrence.to_rrule()
    await ctx.session.flush()
    return {"updated": _view(reminder, ZoneInfo(ctx.user.timezone))}


async def snooze(ctx: ToolContext, args: SnoozeReminderArgs) -> dict[str, Any]:
    try:
        reminder = await snooze_reminder(
            ctx.session, ctx.user, args.reminder_id, args.minutes, ctx.now
        )
    except ReminderNotFoundError as exc:
        raise ToolError(NOT_FOUND) from exc
    return {"snoozed": _view(reminder, ZoneInfo(ctx.user.timezone))}


REMINDER_TOOLS = [
    Tool(
        "create_reminder",
        "Cria um lembrete que será enviado no WhatsApp na data e hora indicadas, "
        "opcionalmente recorrente.",
        CreateReminderArgs,
        create,
    ),
    Tool("list_reminders", "Lista os lembretes pendentes do usuário.", ListRemindersArgs, list_all),
    Tool(
        "cancel_reminder",
        "Cancela um lembrete pendente (em recorrentes, cancela a série).",
        CancelReminderArgs,
        cancel,
    ),
    Tool(
        "update_reminder",
        "Altera texto, data e hora ou repetição de um lembrete pendente.",
        UpdateReminderArgs,
        update,
    ),
    Tool(
        "snooze_reminder",
        "Adia um lembrete por alguns minutos, por padrão o último que foi enviado.",
        SnoozeReminderArgs,
        snooze,
    ),
]

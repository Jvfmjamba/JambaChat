from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from jambachat.reminders.service import ReminderNotFoundError, snooze_reminder
from jambachat.users.models import User

SNOOZE_10 = "Adiar 10 min"
SNOOZE_60 = "Adiar 1 hora"
DONE = "Feito ✅"
REMINDER_BUTTONS = (SNOOZE_10, SNOOZE_60, DONE)
SNOOZE_MINUTES = {SNOOZE_10: 10, SNOOZE_60: 60}

DONE_TEXT = "Boa! ✅"
NOTHING_TO_SNOOZE_TEXT = "Não achei lembrete recente pra adiar."


async def handle_reminder_button(
    session: AsyncSession, user: User, title: str, now: datetime
) -> str | None:
    """Responde aos botões enviados junto com os lembretes, sem passar pelo modelo."""
    if title == DONE:
        return DONE_TEXT
    minutes = SNOOZE_MINUTES.get(title)
    if minutes is None:
        return None
    try:
        reminder = await snooze_reminder(session, user, None, minutes, now)
    except ReminderNotFoundError:
        return NOTHING_TO_SNOOZE_TEXT
    await session.flush()
    local = reminder.due_at.astimezone(ZoneInfo(user.timezone))
    return f"Beleza, te lembro de novo às {local:%H:%M}."

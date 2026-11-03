import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from jambachat.reminders.models import Reminder, ReminderStatus
from jambachat.timeutils import as_utc
from jambachat.users.models import User

MAX_PENDING_PER_USER = 100


class ReminderNotFoundError(Exception):
    pass


class ReminderLimitError(Exception):
    pass


async def count_pending(session: AsyncSession, user: User) -> int:
    total = await session.scalar(
        select(func.count())
        .select_from(Reminder)
        .where(Reminder.user_id == user.id, Reminder.status == ReminderStatus.PENDING)
    )
    return int(total or 0)


async def create_reminder(
    session: AsyncSession, user: User, text: str, due_at: datetime, recurrence: str | None
) -> Reminder:
    if await count_pending(session, user) >= MAX_PENDING_PER_USER:
        raise ReminderLimitError
    reminder = Reminder(user_id=user.id, text=text, due_at=due_at, recurrence=recurrence)
    session.add(reminder)
    await session.flush()
    return reminder


async def list_pending(session: AsyncSession, user: User) -> list[Reminder]:
    rows = await session.scalars(
        select(Reminder)
        .where(Reminder.user_id == user.id, Reminder.status == ReminderStatus.PENDING)
        .order_by(Reminder.due_at)
    )
    return list(rows)


async def get_owned(session: AsyncSession, user: User, reminder_id: str) -> Reminder:
    try:
        key = uuid.UUID(reminder_id)
    except ValueError as exc:
        raise ReminderNotFoundError from exc
    reminder = await session.get(Reminder, key)
    if reminder is None or reminder.user_id != user.id:
        raise ReminderNotFoundError
    return reminder


async def cancel_reminder(session: AsyncSession, user: User, reminder_id: str) -> Reminder:
    reminder = await get_owned(session, user, reminder_id)
    if reminder.status != ReminderStatus.PENDING:
        raise ReminderNotFoundError
    reminder.status = ReminderStatus.CANCELLED
    return reminder


async def last_sent(session: AsyncSession, user: User) -> Reminder | None:
    return await session.scalar(
        select(Reminder)
        .where(Reminder.user_id == user.id, Reminder.status == ReminderStatus.SENT)
        .order_by(Reminder.sent_at.desc())
        .limit(1)
    )


async def snooze_reminder(
    session: AsyncSession, user: User, reminder_id: str | None, minutes: int, now: datetime
) -> Reminder:
    source = await get_owned(session, user, reminder_id) if reminder_id else None
    source = source or await last_sent(session, user)
    if source is None:
        raise ReminderNotFoundError
    due_at = now + timedelta(minutes=minutes)
    if source.status == ReminderStatus.PENDING and source.recurrence is None:
        return _reschedule(source, due_at)
    previous = await _previous_snooze(session, user, source)
    if previous is not None:
        return _reschedule(previous, due_at)
    return await create_reminder(session, user, source.text, due_at, None)


def _reschedule(reminder: Reminder, due_at: datetime) -> Reminder:
    reminder.due_at = due_at
    reminder.attempts = 0
    reminder.next_attempt_at = None
    return reminder


async def _previous_snooze(session: AsyncSession, user: User, source: Reminder) -> Reminder | None:
    """Adiamento já criado para o mesmo lembrete, para um segundo toque não duplicar."""
    if source.sent_at is None:
        return None
    sent_at = as_utc(source.sent_at)
    for candidate in await list_pending(session, user):
        if (
            candidate.id != source.id
            and candidate.recurrence is None
            and candidate.text == source.text
            and as_utc(candidate.created_at) >= sent_at
        ):
            return candidate
    return None

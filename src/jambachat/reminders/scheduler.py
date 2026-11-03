import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jambachat.config import Settings
from jambachat.conversation.history import save_turn
from jambachat.llm.types import ChatMessage
from jambachat.reminders.models import Reminder, ReminderStatus
from jambachat.reminders.recurrence import next_occurrence
from jambachat.timeutils import as_utc
from jambachat.users.models import User
from jambachat.whatsapp.client import WhatsAppClient, WhatsAppError

logger = structlog.get_logger(__name__)

MAX_ATTEMPTS = 3
RETRY_DELAY = timedelta(minutes=2)
SERVICE_WINDOW = timedelta(hours=24)
BATCH_SIZE = 50


def reminder_text(text: str) -> str:
    return f"*Lembrete:* {text}"


class ReminderDispatcher:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        whatsapp: WhatsAppClient,
        settings: Settings,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._whatsapp = whatsapp
        self._settings = settings

    async def run_once(self, now: datetime | None = None) -> int:
        now = now or datetime.now(UTC)
        async with self._sessionmaker() as session:
            due = list(
                await session.scalars(
                    select(Reminder)
                    .where(
                        Reminder.status == ReminderStatus.PENDING,
                        Reminder.due_at <= now,
                        or_(Reminder.next_attempt_at.is_(None), Reminder.next_attempt_at <= now),
                    )
                    .order_by(Reminder.due_at)
                    .limit(BATCH_SIZE)
                    .with_for_update(skip_locked=True)
                )
            )
            for reminder in due:
                await self._dispatch(session, reminder, now)
            await session.commit()
        return len(due)

    async def _dispatch(self, session: AsyncSession, reminder: Reminder, now: datetime) -> None:
        log = logger.bind(reminder_id=str(reminder.id), user_id=str(reminder.user_id))
        user = await session.get(User, reminder.user_id)
        if user is None:
            reminder.status = ReminderStatus.CANCELLED
            return
        try:
            await self._send(user, reminder.text, now)
        except WhatsAppError as exc:
            reminder.attempts += 1
            if reminder.attempts >= MAX_ATTEMPTS:
                reminder.status = ReminderStatus.FAILED
            else:
                reminder.next_attempt_at = now + RETRY_DELAY
            log.warning("reminder_send_failed", attempts=reminder.attempts, error=str(exc))
            return

        reminder.status = ReminderStatus.SENT
        reminder.sent_at = now
        await save_turn(session, user.id, [ChatMessage("assistant", reminder_text(reminder.text))])
        if reminder.recurrence:
            upcoming = next_occurrence(
                reminder.recurrence, reminder.due_at, now, ZoneInfo(user.timezone)
            )
            if upcoming is not None:
                session.add(
                    Reminder(
                        user_id=user.id,
                        text=reminder.text,
                        due_at=upcoming,
                        recurrence=reminder.recurrence,
                    )
                )
        log.info("reminder_sent")

    async def _send(self, user: User, text: str, now: datetime) -> None:
        last_inbound = user.last_inbound_at
        if last_inbound is not None and now - as_utc(last_inbound) < SERVICE_WINDOW:
            await self._whatsapp.send_text(user.phone, reminder_text(text))
            return
        await self._whatsapp.send_template(
            user.phone, self._settings.whatsapp_reminder_template, [" ".join(text.split())]
        )


class ReminderScheduler:
    def __init__(self, dispatcher: ReminderDispatcher, interval_seconds: float) -> None:
        self._dispatcher = dispatcher
        self._interval = interval_seconds
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="reminder-scheduler")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self._dispatcher.run_once()
            except Exception:
                logger.exception("scheduler_iteration_failed")
            await asyncio.sleep(self._interval)

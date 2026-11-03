import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jambachat.conversation.models import Message, Role
from jambachat.reminders.models import Reminder, ReminderStatus
from jambachat.reminders.scheduler import ReminderDispatcher, ReminderScheduler
from jambachat.users.models import User
from jambachat.whatsapp.client import WhatsAppClient
from tests.fakes import GRAPH_MESSAGES_URL, graph_ok, make_settings

Maker = async_sessionmaker[AsyncSession]
NOW = datetime(2026, 11, 3, 15, 0, tzinfo=UTC)


async def seed(
    sessionmaker: Maker,
    *,
    last_inbound: datetime | None,
    recurrence: str | None = None,
    due: datetime = NOW,
) -> None:
    async with sessionmaker() as session:
        user = User(phone="5511", phone_hash="h", last_inbound_at=last_inbound)
        session.add(user)
        await session.flush()
        session.add(
            Reminder(user_id=user.id, text="pagar\na luz", due_at=due, recurrence=recurrence)
        )
        await session.commit()


async def dispatch(
    sessionmaker: Maker, response: httpx.Response | None = None, now: datetime = NOW
) -> list[dict[str, Any]]:
    with respx.mock:
        route = respx.post(GRAPH_MESSAGES_URL)
        if response is None:
            route.mock(side_effect=graph_ok)
        else:
            route.mock(return_value=response)
        async with httpx.AsyncClient() as http:
            whatsapp = WhatsAppClient(
                http, access_token="t", phone_number_id="123", api_version="v23.0"
            )
            await ReminderDispatcher(sessionmaker, whatsapp, make_settings()).run_once(now)
        return [json.loads(c.request.content) for c in route.calls]


async def reminders(sessionmaker: Maker) -> list[Reminder]:
    async with sessionmaker() as session:
        return list(await session.scalars(select(Reminder).order_by(Reminder.due_at)))


async def test_inside_window_sends_text_and_records_history(sessionmaker: Maker) -> None:
    await seed(sessionmaker, last_inbound=NOW - timedelta(hours=1))
    sent = await dispatch(sessionmaker)

    assert sent[0]["type"] == "text"
    assert sent[0]["text"]["body"] == "*Lembrete:* pagar\na luz"
    [reminder] = await reminders(sessionmaker)
    assert reminder.status == ReminderStatus.SENT
    async with sessionmaker() as session:
        assert list(await session.scalars(select(Message.role))) == [Role.ASSISTANT]


async def test_outside_window_uses_template(sessionmaker: Maker) -> None:
    await seed(sessionmaker, last_inbound=NOW - timedelta(days=2))
    sent = await dispatch(sessionmaker)

    assert sent[0]["type"] == "template"
    assert sent[0]["template"]["components"][0]["parameters"][0]["text"] == "pagar a luz"


async def test_recurring_reminder_schedules_next(sessionmaker: Maker) -> None:
    await seed(sessionmaker, last_inbound=None, recurrence="FREQ=DAILY;INTERVAL=1")
    await dispatch(sessionmaker)

    first, second = await reminders(sessionmaker)
    assert first.status == ReminderStatus.SENT
    assert second.status == ReminderStatus.PENDING
    assert second.due_at.replace(tzinfo=UTC) == NOW + timedelta(days=1)


async def test_failures_retry_then_give_up(sessionmaker: Maker) -> None:
    await seed(sessionmaker, last_inbound=None)
    error = httpx.Response(500, json={})

    assert len(await dispatch(sessionmaker, error)) == 1
    assert await dispatch(sessionmaker, error, NOW + timedelta(minutes=1)) == []
    await dispatch(sessionmaker, error, NOW + timedelta(minutes=3))
    await dispatch(sessionmaker, error, NOW + timedelta(minutes=6))

    [reminder] = await reminders(sessionmaker)
    assert reminder.attempts == 3
    assert reminder.status == ReminderStatus.FAILED


async def test_future_reminders_are_left_alone(sessionmaker: Maker) -> None:
    await seed(sessionmaker, last_inbound=None, due=NOW + timedelta(minutes=5))

    assert await dispatch(sessionmaker) == []


async def test_scheduler_loop_survives_errors() -> None:
    calls = 0

    class Flaky:
        async def run_once(self) -> int:
            nonlocal calls
            calls += 1
            raise RuntimeError("falhou")

    scheduler = ReminderScheduler(Flaky(), 0.01)  # type: ignore[arg-type]
    scheduler.start()
    await asyncio.sleep(0.05)
    await scheduler.stop()

    assert calls >= 2

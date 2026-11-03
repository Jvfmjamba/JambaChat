import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jambachat.llm.types import ToolCall
from jambachat.reminders.buttons import button_ids, handle_reminder_button
from jambachat.reminders.models import Reminder, ReminderStatus
from jambachat.tools.catalog import build_registry
from jambachat.tools.registry import ToolContext
from jambachat.users.models import User
from tests.fakes import make_settings

NOW = datetime(2026, 11, 3, 15, 0, tzinfo=UTC)


@pytest.fixture
async def session(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with sessionmaker() as s:
        yield s


async def new_user(session: AsyncSession, phone: str) -> User:
    user = User(phone=phone, phone_hash=phone, timezone="America/Sao_Paulo")
    session.add(user)
    await session.flush()
    return user


async def run(session: AsyncSession, user: User, name: str, **args: Any) -> dict[str, Any]:
    ctx = ToolContext(
        session=session,
        user=user,
        settings=make_settings(),
        now=NOW.astimezone(ZoneInfo(user.timezone)),
    )
    result: dict[str, Any] = json.loads(
        await build_registry().execute(ToolCall("c", name, args), ctx)
    )
    return result


async def test_create_list_and_cancel(session: AsyncSession) -> None:
    user = await new_user(session, "1")
    created = await run(
        session,
        user,
        "create_reminder",
        text="pagar a luz",
        due_at="2026-11-10T09:00",
        recurrence={"frequency": "mensal"},
    )
    await run(session, user, "create_reminder", text="dentista", due_at="2026-11-05T14:30")

    assert created["created"]["due_at"] == "ter, 10/11/2026 às 09:00"
    assert created["created"]["recurrence"] == "todo mês"

    listed = await run(session, user, "list_reminders")
    assert [r["text"] for r in listed["reminders"]] == ["dentista", "pagar a luz"]

    reminder_id = created["created"]["id"]
    assert "cancelled" in await run(session, user, "cancel_reminder", reminder_id=reminder_id)
    assert (await run(session, user, "list_reminders"))["count"] == 1
    again = await run(session, user, "cancel_reminder", reminder_id=reminder_id)
    assert "não encontrado" in again["error"]


@pytest.mark.parametrize(
    ("due_at", "message"),
    [
        ("2026-11-01T09:00", "já passou"),
        ("amanhã", "data inválida"),
        ("2035-01-01T09:00", "5 anos"),
    ],
)
async def test_create_rejects_bad_dates(session: AsyncSession, due_at: str, message: str) -> None:
    user = await new_user(session, "1")
    result = await run(session, user, "create_reminder", text="x", due_at=due_at)

    assert message in result["error"]


async def test_users_cannot_touch_each_other_reminders(session: AsyncSession) -> None:
    owner = await new_user(session, "1")
    intruder = await new_user(session, "2")
    created = await run(session, owner, "create_reminder", text="x", due_at="2026-11-10T09:00")
    reminder_id = created["created"]["id"]

    for name, args in [
        ("cancel_reminder", {"reminder_id": reminder_id}),
        ("snooze_reminder", {"reminder_id": reminder_id, "minutes": 5}),
        ("cancel_reminder", {"reminder_id": "nao-e-uuid"}),
    ]:
        assert "não encontrado" in (await run(session, intruder, name, **args))["error"]
    assert (await run(session, intruder, "list_reminders"))["count"] == 0


async def test_snooze_last_sent_reminder_creates_new_one(session: AsyncSession) -> None:
    user = await new_user(session, "1")
    assert "error" in await run(session, user, "snooze_reminder", minutes=10)

    session.add(
        Reminder(
            user_id=user.id,
            text="tomar remédio",
            due_at=NOW - timedelta(minutes=1),
            status=ReminderStatus.SENT,
            sent_at=NOW - timedelta(minutes=1),
        )
    )
    await session.flush()
    result = await run(session, user, "snooze_reminder", minutes=10)

    assert result["snoozed"]["text"] == "tomar remédio"
    assert result["snoozed"]["due_at"] == "ter, 03/11/2026 às 12:10"


async def test_pending_reminders_are_capped(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jambachat.reminders import service

    monkeypatch.setattr(service, "MAX_PENDING_PER_USER", 2)
    user = await new_user(session, "1")
    for _ in range(2):
        await run(session, user, "create_reminder", text="x", due_at="2026-11-10T09:00")
    blocked = await run(session, user, "create_reminder", text="x", due_at="2026-11-10T09:00")

    assert "limite" in blocked["error"]
    assert (await run(session, user, "list_reminders"))["count"] == 2


async def test_unknown_arguments_are_rejected(session: AsyncSession) -> None:
    user = await new_user(session, "1")
    result = await run(
        session, user, "list_reminders", user_id="00000000-0000-0000-0000-000000000000"
    )

    assert result["error"] == "argumentos inválidos"


async def test_update_reminder(session: AsyncSession) -> None:
    user = await new_user(session, "1")
    created = await run(
        session,
        user,
        "create_reminder",
        text="academia",
        due_at="2026-11-10T07:00",
        recurrence={"frequency": "semanal", "weekdays": ["seg", "qua"]},
    )
    reminder_id = created["created"]["id"]

    updated = await run(
        session,
        user,
        "update_reminder",
        reminder_id=reminder_id,
        text="academia cedo",
        due_at="2026-11-10T06:30",
    )
    assert updated["updated"]["text"] == "academia cedo"
    assert updated["updated"]["due_at"] == "ter, 10/11/2026 às 06:30"
    assert updated["updated"]["recurrence"] is not None

    plain = await run(
        session, user, "update_reminder", reminder_id=reminder_id, remove_recurrence=True
    )
    assert plain["updated"]["recurrence"] is None

    past = await run(
        session, user, "update_reminder", reminder_id=reminder_id, due_at="2026-11-01T09:00"
    )
    assert "já passou" in past["error"]

    await run(session, user, "cancel_reminder", reminder_id=reminder_id)
    gone = await run(session, user, "update_reminder", reminder_id=reminder_id, text="x")
    assert "não encontrado" in gone["error"]


async def test_buttons_snooze_the_tapped_reminder_once(session: AsyncSession) -> None:
    user = await new_user(session, "1")
    now = datetime.now(UTC)
    older = Reminder(
        user_id=user.id,
        text="remédio",
        due_at=NOW,
        status=ReminderStatus.SENT,
        sent_at=now - timedelta(minutes=5),
    )
    newer = Reminder(
        user_id=user.id,
        text="reunião",
        due_at=NOW,
        status=ReminderStatus.SENT,
        sent_at=now,
    )
    session.add_all([older, newer])
    await session.flush()
    snooze_id = button_ids(older)[0]

    for _ in range(2):
        reply = await handle_reminder_button(session, user, "Adiar 10 min", now, snooze_id)
        assert reply is not None and reply.startswith("Beleza")

    listed = await run(session, user, "list_reminders")
    assert [r["text"] for r in listed["reminders"]] == ["remédio"]
    assert await handle_reminder_button(session, user, "outra coisa", now) is None

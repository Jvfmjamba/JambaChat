from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from jambachat.reminders.recurrence import Recurrence, describe, next_occurrence
from jambachat.timeutils import format_local, parse_local_datetime

SP = ZoneInfo("America/Sao_Paulo")


def test_rrule_generation_orders_weekdays() -> None:
    rule = Recurrence(frequency="semanal", weekdays=["sex", "seg"]).to_rrule()

    assert rule == "FREQ=WEEKLY;INTERVAL=1;BYDAY=MO,FR"


@pytest.mark.parametrize(
    ("rule", "text"),
    [
        ("FREQ=DAILY;INTERVAL=1", "todo dia"),
        ("FREQ=DAILY;INTERVAL=3", "a cada 3 dias"),
        ("FREQ=WEEKLY;INTERVAL=1;BYDAY=MO,SA", "toda semana (seg, sáb)"),
        ("FREQ=MONTHLY;INTERVAL=1", "todo mês"),
    ],
)
def test_describe(rule: str, text: str) -> None:
    assert describe(rule) == text


def test_next_weekly_occurrence_keeps_local_time() -> None:
    anchor = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
    upcoming = next_occurrence("FREQ=WEEKLY;BYDAY=MO,WE", anchor, anchor, SP)

    assert upcoming == datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


def test_next_occurrence_skips_missed_ones() -> None:
    anchor = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
    after = datetime(2026, 11, 5, 13, 0, tzinfo=UTC)

    assert next_occurrence("FREQ=DAILY", anchor, after, SP) == datetime(
        2026, 11, 6, 12, 0, tzinfo=UTC
    )


def test_monthly_occurrence() -> None:
    anchor = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)

    assert next_occurrence("FREQ=MONTHLY", anchor, anchor, SP) == datetime(
        2026, 12, 10, 12, 0, tzinfo=UTC
    )


def test_parse_and_format_local_datetime() -> None:
    parsed = parse_local_datetime("2026-11-07T09:00", SP)

    assert parsed == datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
    assert parse_local_datetime("2026-11-07T09:00+00:00", SP).hour == 9
    assert format_local(parsed, SP) == "sáb, 07/11/2026 às 09:00"
    assert format_local(parsed.replace(tzinfo=None), SP) == "sáb, 07/11/2026 às 09:00"

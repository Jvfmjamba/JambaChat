from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

WEEKDAY_SHORT = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def parse_local_datetime(value: str, tz: ZoneInfo) -> datetime:
    parsed = datetime.fromisoformat(value.strip())
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(UTC)


def format_local(value: datetime, tz: ZoneInfo) -> str:
    local = as_utc(value).astimezone(tz)
    return f"{WEEKDAY_SHORT[local.weekday()]}, {local:%d/%m/%Y} às {local:%H:%M}"


def parse_local_date(value: str) -> date:
    return date.fromisoformat(value.strip()[:10])


def local_range(start: date, end: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    lower = datetime.combine(start, time.min, tz).astimezone(UTC)
    upper = datetime.combine(end + timedelta(days=1), time.min, tz).astimezone(UTC)
    return lower, upper


def month_start(day: date) -> date:
    return day.replace(day=1)


def format_local_date(value: datetime, tz: ZoneInfo) -> str:
    local = as_utc(value).astimezone(tz)
    return f"{WEEKDAY_SHORT[local.weekday()]}, {local:%d/%m/%Y}"

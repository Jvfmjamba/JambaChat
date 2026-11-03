from datetime import date, timedelta

from dateutil.relativedelta import relativedelta

WEEKDAYS = (
    "segunda-feira",
    "terça-feira",
    "quarta-feira",
    "quinta-feira",
    "sexta-feira",
    "sábado",
    "domingo",
)


def business_days_between(start: date, end: date) -> int:
    """Dias úteis (seg a sex) no intervalo (start, end], negativo se end < start."""
    sign = 1
    if end < start:
        start, end, sign = end, start, -1
    days = (end - start).days
    weeks, rest = divmod(days, 7)
    count = weeks * 5
    for offset in range(1, rest + 1):
        if (start + timedelta(days=offset)).weekday() < 5:
            count += 1
    return sign * count


def describe_gap(start: date, end: date) -> dict[str, int]:
    delta = relativedelta(end, start)
    return {
        "days": (end - start).days,
        "weeks": (end - start).days // 7,
        "business_days": business_days_between(start, end),
        "years": delta.years,
        "months": delta.months,
        "remaining_days": delta.days,
    }


def shift(start: date, *, days: int = 0, weeks: int = 0, months: int = 0, years: int = 0) -> date:
    return start + relativedelta(days=days, weeks=weeks, months=months, years=years)


def describe_date(value: date) -> str:
    return f"{WEEKDAYS[value.weekday()]}, {value:%d/%m/%Y}"

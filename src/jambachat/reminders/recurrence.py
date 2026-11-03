from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from dateutil.rrule import rrulestr
from pydantic import BaseModel, ConfigDict, Field

from jambachat.timeutils import as_utc

Frequency = Literal["diaria", "semanal", "mensal", "anual"]
Weekday = Literal["seg", "ter", "qua", "qui", "sex", "sab", "dom"]

FREQ_TO_RRULE = {"diaria": "DAILY", "semanal": "WEEKLY", "mensal": "MONTHLY", "anual": "YEARLY"}
DAY_TO_RRULE = {
    "seg": "MO",
    "ter": "TU",
    "qua": "WE",
    "qui": "TH",
    "sex": "FR",
    "sab": "SA",
    "dom": "SU",
}
RRULE_TO_DAY = {v: k for k, v in DAY_TO_RRULE.items()}
SINGULAR = {
    "DAILY": "todo dia",
    "WEEKLY": "toda semana",
    "MONTHLY": "todo mês",
    "YEARLY": "todo ano",
}
PLURAL = {"DAILY": "dias", "WEEKLY": "semanas", "MONTHLY": "meses", "YEARLY": "anos"}


class Recurrence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frequency: Frequency = Field(description="Frequência da repetição")
    interval: int = Field(default=1, ge=1, le=366, description="A cada quantas unidades repete")
    weekdays: list[Weekday] = Field(
        default_factory=list, description="Dias da semana, só para frequência semanal"
    )

    def to_rrule(self) -> str:
        parts = [f"FREQ={FREQ_TO_RRULE[self.frequency]}", f"INTERVAL={self.interval}"]
        if self.weekdays:
            ordered = [d for d in DAY_TO_RRULE if d in self.weekdays]
            parts.append("BYDAY=" + ",".join(DAY_TO_RRULE[d] for d in ordered))
        return ";".join(parts)


def describe(rule: str) -> str:
    fields = dict(part.split("=", 1) for part in rule.split(";"))
    freq = fields["FREQ"]
    interval = int(fields.get("INTERVAL", "1"))
    text = SINGULAR[freq] if interval == 1 else f"a cada {interval} {PLURAL[freq]}"
    if "BYDAY" in fields:
        days = [RRULE_TO_DAY[d].replace("sab", "sáb") for d in fields["BYDAY"].split(",")]
        text += f" ({', '.join(days)})"
    return text


def next_occurrence(rule: str, anchor: datetime, after: datetime, tz: ZoneInfo) -> datetime | None:
    start = as_utc(anchor).astimezone(tz)
    pivot = max(as_utc(after).astimezone(tz), start)
    occurrence = rrulestr(rule, dtstart=start).after(pivot)
    return None if occurrence is None else as_utc(occurrence)

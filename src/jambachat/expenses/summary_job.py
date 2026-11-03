from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jambachat.conversation.history import save_turn
from jambachat.expenses.service import EntryFilter, find_entries, summarize
from jambachat.llm.types import ChatMessage
from jambachat.money import format_brl
from jambachat.timeutils import as_utc, local_range
from jambachat.users.models import User
from jambachat.whatsapp.client import WhatsAppClient, WhatsAppError

logger = structlog.get_logger(__name__)

SEND_FROM_HOUR = 9
SERVICE_WINDOW = timedelta(hours=24)
MONTHS = (
    "janeiro",
    "fevereiro",
    "março",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
)


def previous_month(today: date) -> tuple[date, date]:
    end = today.replace(day=1) - timedelta(days=1)
    return end.replace(day=1), end


def summary_text(month: date, expense: int, income: int, top: list[tuple[str, int]]) -> str:
    lines = [f"📊 *Resumo de {MONTHS[month.month - 1]}*", f"• Gastos: {format_brl(expense)}"]
    if income:
        lines.append(f"• Receitas: {format_brl(income)}")
        lines.append(f"• Saldo: {format_brl(income - expense)}")
    if top:
        lines.append("")
        lines.append("Onde mais foi:")
        lines.extend(f"• {name}: {format_brl(cents)}" for name, cents in top)
    return "\n".join(lines)


class MonthlySummaryJob:
    """No dia 1º, manda o resumo do mês anterior para quem está na janela de 24h."""

    def __init__(
        self, sessionmaker: async_sessionmaker[AsyncSession], whatsapp: WhatsAppClient
    ) -> None:
        self._sessionmaker = sessionmaker
        self._whatsapp = whatsapp

    async def run_once(self, now: datetime | None = None) -> int:
        now = now or datetime.now(UTC)
        sent = 0
        async with self._sessionmaker() as session:
            users = list(
                await session.scalars(
                    select(User).where(User.last_inbound_at >= now - SERVICE_WINDOW)
                )
            )
            for user in users:
                try:
                    if await self._maybe_send(session, user, now):
                        sent += 1
                except WhatsAppError as exc:
                    logger.warning("monthly_summary_failed", user_id=str(user.id), error=str(exc))
            await session.commit()
        return sent

    async def _maybe_send(self, session: AsyncSession, user: User, now: datetime) -> bool:
        tz = ZoneInfo(user.timezone)
        local = now.astimezone(tz)
        if local.day != 1 or local.hour < SEND_FROM_HOUR:
            return False
        if user.last_inbound_at is None or now - as_utc(user.last_inbound_at) > SERVICE_WINDOW:
            return False
        start, end = previous_month(local.date())
        key = f"{start:%Y-%m}"
        if user.last_summary_month == key:
            return False
        lower, upper = local_range(start, end, tz)
        summary = summarize(await find_entries(session, user, EntryFilter(lower, upper)))
        user.last_summary_month = key
        if summary.count == 0:
            return False
        top = sorted(summary.by_category.items(), key=lambda item: item[1], reverse=True)[:3]
        text = summary_text(start, summary.expense_cents, summary.income_cents, top)
        await self._whatsapp.send_text(user.phone, text)
        await save_turn(session, user.id, [ChatMessage("assistant", text)])
        logger.info("monthly_summary_sent", user_id=str(user.id), month=key)
        return True

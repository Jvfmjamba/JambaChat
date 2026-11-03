from datetime import UTC, date, datetime, timedelta

import httpx
import respx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jambachat.expenses.models import EntryKind, Expense
from jambachat.expenses.summary_job import MonthlySummaryJob, previous_month, summary_text
from jambachat.users.models import User
from jambachat.whatsapp.client import WhatsAppClient
from tests.fakes import GRAPH_MESSAGES_URL, graph_ok, sent_texts

Maker = async_sessionmaker[AsyncSession]
# 1º de dezembro, 10h em São Paulo
FIRST = datetime(2026, 12, 1, 13, 0, tzinfo=UTC)


def test_previous_month_and_text() -> None:
    assert previous_month(date(2026, 1, 1)) == (date(2025, 12, 1), date(2025, 12, 31))
    text = summary_text(date(2026, 11, 1), 15000, 0, [("Mercado", 10000)])
    assert text.splitlines()[0] == "📊 *Resumo de novembro*"
    assert "Receitas" not in text
    assert "• Mercado: R$ 100,00" in text


async def seed(sessionmaker: Maker, last_inbound: datetime) -> None:
    async with sessionmaker() as session:
        user = User(phone="5511", phone_hash="h", timezone="America/Sao_Paulo")
        user.last_inbound_at = last_inbound
        session.add(user)
        await session.flush()
        for amount, category, kind, day in [
            (12000, "Mercado", EntryKind.EXPENSE, datetime(2026, 11, 10, 15, tzinfo=UTC)),
            (3000, "Lazer", EntryKind.EXPENSE, datetime(2026, 11, 20, 15, tzinfo=UTC)),
            (500000, "Salário", EntryKind.INCOME, datetime(2026, 11, 5, 15, tzinfo=UTC)),
            (999, "Lazer", EntryKind.EXPENSE, datetime(2026, 12, 1, 12, tzinfo=UTC)),
        ]:
            session.add(
                Expense(
                    user_id=user.id,
                    kind=kind,
                    amount_cents=amount,
                    description="x",
                    category=category,
                    occurred_at=day,
                )
            )
        await session.commit()


async def run(sessionmaker: Maker, *moments: datetime) -> tuple[list[int], list[str]]:
    with respx.mock:
        route = respx.post(GRAPH_MESSAGES_URL).mock(side_effect=graph_ok)
        async with httpx.AsyncClient() as http:
            whatsapp = WhatsAppClient(
                http, access_token="t", phone_number_id="123", api_version="v23.0"
            )
            job = MonthlySummaryJob(sessionmaker, whatsapp)
            counts = [await job.run_once(moment) for moment in moments]
        return counts, sent_texts(route)


async def test_sends_once_on_the_first_after_nine(sessionmaker: Maker) -> None:
    await seed(sessionmaker, FIRST - timedelta(hours=2))
    counts, texts = await run(
        sessionmaker,
        FIRST - timedelta(hours=2),
        FIRST,
        FIRST + timedelta(hours=1),
        FIRST + timedelta(days=1),
    )

    assert counts == [0, 1, 0, 0]
    [text] = texts
    assert "Gastos: R$ 150,00" in text
    assert "Saldo: R$ 4.850,00" in text
    assert text.index("Mercado") < text.index("Lazer")


async def test_skips_users_outside_the_window(sessionmaker: Maker) -> None:
    await seed(sessionmaker, FIRST - timedelta(days=2))
    counts, texts = await run(sessionmaker, FIRST)
    assert counts == [0]
    assert texts == []

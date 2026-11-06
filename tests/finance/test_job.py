from datetime import UTC, date, datetime
from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jambachat.container import build_container
from jambachat.finance.errors import FinanceAuthError
from jambachat.finance.importer import FinanceImporter
from jambachat.finance.job import FinanceImportJob
from jambachat.finance.models import FinanceConnection
from jambachat.finance.provider import NullFinanceProvider, RawTransaction
from jambachat.users.models import User
from jambachat.whatsapp.client import WhatsAppClient
from tests.fakes import FakeFinanceProvider, make_settings


class _WhatsApp:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send_text(self, phone: str, text: str) -> None:
        self.messages.append(text)


class _Provider(FakeFinanceProvider):
    async def fetch_transactions(
        self, item_ref: str, since: date, until: date
    ) -> list[RawTransaction]:
        if item_ref == "expired":
            raise FinanceAuthError
        if item_ref == "failed":
            raise RuntimeError
        return await super().fetch_transactions(item_ref, since, until)


async def _connection(session: AsyncSession, phone: str, item_ref: str) -> FinanceConnection:
    user = User(phone=phone, phone_hash=phone)
    session.add(user)
    await session.flush()
    connection = FinanceConnection(user_id=user.id, item_ref=item_ref, institution="Banco")
    session.add(connection)
    await session.flush()
    return connection


async def test_job_isolates_failures_and_notifies_reconnect_once(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker() as session:
        expired = await _connection(session, "1", "expired")
        await _connection(session, "2", "failed")
        imported = await _connection(session, "3", "ok")
        await session.commit()
    whatsapp = _WhatsApp()
    provider = _Provider(
        transactions={
            "ok": [
                RawTransaction(
                    "tx", date(2026, 11, 5), 100, "Salario", None, "income", "POSTED", False
                )
            ]
        }
    )
    job = FinanceImportJob(sessionmaker, FinanceImporter(provider), cast(WhatsAppClient, whatsapp))
    now = datetime(2026, 11, 6, tzinfo=UTC)

    assert await job.run_once(now) == 1
    assert await job.run_once(now) == 0
    async with sessionmaker() as session:
        expired_connection = await session.get(FinanceConnection, expired.id)
        imported_connection = await session.get(FinanceConnection, imported.id)
        assert expired_connection is not None
        assert imported_connection is not None
        assert expired_connection.status == "reconnect"
        assert imported_connection.last_synced_at == now.replace(tzinfo=None)
    assert len(whatsapp.messages) == 1


async def test_container_without_credentials_disables_finance_job() -> None:
    container = build_container(make_settings(scheduler_enabled=True))
    try:
        assert isinstance(container.finance_provider, NullFinanceProvider)
        assert container.scheduler is not None
        assert not any(isinstance(job, FinanceImportJob) for job in container.scheduler._jobs)
    finally:
        await container.aclose()

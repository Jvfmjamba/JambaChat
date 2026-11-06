from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jambachat.config import Settings
from jambachat.conversation.orchestrator import Orchestrator
from jambachat.conversation.service import ConversationService
from jambachat.db.session import get_engine, get_sessionmaker
from jambachat.expenses.recurring import RecurringEntryJob
from jambachat.expenses.summary_job import MonthlySummaryJob
from jambachat.finance.importer import FinanceImporter
from jambachat.finance.job import FinanceImportJob
from jambachat.finance.pluggy import PluggyProvider
from jambachat.finance.provider import FinanceProvider, NullFinanceProvider
from jambachat.llm.factory import create_llm_client
from jambachat.media.transcriber import Transcriber
from jambachat.reminders.scheduler import ReminderDispatcher, ReminderScheduler
from jambachat.security.crypto import get_cipher
from jambachat.security.ratelimit import RateLimiter
from jambachat.tools.catalog import build_registry
from jambachat.wallet.service import WalletService
from jambachat.whatsapp.client import WhatsAppClient


@dataclass
class Container:
    settings: Settings
    sessionmaker: async_sessionmaker[AsyncSession]
    conversation: ConversationService
    scheduler: ReminderScheduler | None = None
    wallet: WalletService | None = None
    finance_provider: FinanceProvider = field(default_factory=NullFinanceProvider)
    closers: list[Callable[[], Awaitable[None]]] = field(default_factory=list)

    def start(self) -> None:
        if self.scheduler is not None:
            self.scheduler.start()

    async def aclose(self) -> None:
        if self.scheduler is not None:
            await self.scheduler.stop()
        for close in self.closers:
            await close()


class InsecureConfigError(RuntimeError):
    pass


def build_container(settings: Settings) -> Container:
    if settings.is_production and (problems := settings.production_problems()):
        raise InsecureConfigError("configuração insegura para produção: " + "; ".join(problems))
    get_cipher()
    http = httpx.AsyncClient(follow_redirects=False)
    sessionmaker = get_sessionmaker()
    whatsapp = WhatsAppClient(
        http,
        access_token=settings.whatsapp_access_token.get_secret_value(),
        phone_number_id=settings.whatsapp_phone_number_id,
        api_version=settings.whatsapp_api_version,
    )
    finance_provider: FinanceProvider = (
        PluggyProvider(
            http,
            client_id=settings.pluggy_client_id,
            client_secret=settings.pluggy_client_secret,
            base_url=settings.pluggy_base_url,
        )
        if settings.pluggy_client_id.get_secret_value()
        else NullFinanceProvider()
    )
    orchestrator = Orchestrator(
        create_llm_client(settings, http),
        build_registry(
            settings,
            http,
            finance_provider if settings.pluggy_client_id.get_secret_value() else None,
        ),
    )
    scheduler = (
        ReminderScheduler(
            ReminderDispatcher(sessionmaker, whatsapp, settings),
            settings.scheduler_interval_seconds,
            [
                RecurringEntryJob(sessionmaker),
                *([MonthlySummaryJob(sessionmaker, whatsapp)] if settings.monthly_summary else []),
                *(
                    [FinanceImportJob(sessionmaker, FinanceImporter(finance_provider), whatsapp)]
                    if settings.finance_import_enabled
                    and settings.pluggy_client_id.get_secret_value()
                    else []
                ),
            ],
        )
        if settings.scheduler_enabled
        else None
    )
    return Container(
        settings=settings,
        sessionmaker=sessionmaker,
        conversation=ConversationService(
            sessionmaker,
            whatsapp,
            orchestrator,
            settings,
            transcriber=_transcriber(settings, http),
            limiter=RateLimiter.from_settings(settings),
            finance_provider=finance_provider,
        ),
        scheduler=scheduler,
        wallet=WalletService(
            sessionmaker, whatsapp, settings, RateLimiter([(10, 60), (200, 86_400)])
        ),
        finance_provider=finance_provider,
        closers=[http.aclose, get_engine().dispose],
    )


def _transcriber(settings: Settings, http: httpx.AsyncClient) -> Transcriber | None:
    if not settings.stt_base_url:
        return None
    return Transcriber(
        http,
        base_url=settings.stt_base_url,
        api_key=settings.stt_api_key.get_secret_value(),
        model=settings.stt_model,
    )

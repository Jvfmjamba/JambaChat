from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jambachat.config import Settings
from jambachat.conversation.orchestrator import Orchestrator
from jambachat.conversation.service import ConversationService
from jambachat.db.session import get_engine, get_sessionmaker
from jambachat.llm.factory import create_llm_client
from jambachat.reminders.scheduler import ReminderDispatcher, ReminderScheduler
from jambachat.security.crypto import get_cipher
from jambachat.tools.catalog import build_registry
from jambachat.whatsapp.client import WhatsAppClient


@dataclass
class Container:
    settings: Settings
    sessionmaker: async_sessionmaker[AsyncSession]
    conversation: ConversationService
    scheduler: ReminderScheduler | None = None
    closers: list[Callable[[], Awaitable[None]]] = field(default_factory=list)

    def start(self) -> None:
        if self.scheduler is not None:
            self.scheduler.start()

    async def aclose(self) -> None:
        if self.scheduler is not None:
            await self.scheduler.stop()
        for close in self.closers:
            await close()


def build_container(settings: Settings) -> Container:
    get_cipher()
    http = httpx.AsyncClient(follow_redirects=False)
    sessionmaker = get_sessionmaker()
    whatsapp = WhatsAppClient(
        http,
        access_token=settings.whatsapp_access_token.get_secret_value(),
        phone_number_id=settings.whatsapp_phone_number_id,
        api_version=settings.whatsapp_api_version,
    )
    orchestrator = Orchestrator(create_llm_client(settings, http), build_registry(settings, http))
    scheduler = (
        ReminderScheduler(
            ReminderDispatcher(sessionmaker, whatsapp, settings),
            settings.scheduler_interval_seconds,
        )
        if settings.scheduler_enabled
        else None
    )
    return Container(
        settings=settings,
        sessionmaker=sessionmaker,
        conversation=ConversationService(sessionmaker, whatsapp, orchestrator, settings),
        scheduler=scheduler,
        closers=[http.aclose, get_engine().dispose],
    )

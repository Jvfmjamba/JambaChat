from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jambachat.config import Settings
from jambachat.conversation.orchestrator import Orchestrator
from jambachat.conversation.service import ConversationService
from jambachat.db.session import get_engine, get_sessionmaker
from jambachat.llm.factory import create_llm_client
from jambachat.security.crypto import get_cipher
from jambachat.tools.catalog import build_registry
from jambachat.whatsapp.client import WhatsAppClient


@dataclass
class Container:
    settings: Settings
    sessionmaker: async_sessionmaker[AsyncSession]
    conversation: ConversationService
    closers: list[Callable[[], Awaitable[None]]] = field(default_factory=list)

    async def aclose(self) -> None:
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
    orchestrator = Orchestrator(create_llm_client(settings, http), build_registry())
    conversation = ConversationService(sessionmaker, whatsapp, orchestrator, settings)
    return Container(
        settings=settings,
        sessionmaker=sessionmaker,
        conversation=conversation,
        closers=[http.aclose, get_engine().dispose],
    )

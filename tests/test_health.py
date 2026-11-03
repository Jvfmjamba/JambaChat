import httpx
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from jambachat.container import Container
from jambachat.conversation.orchestrator import Orchestrator
from jambachat.conversation.service import ConversationService
from jambachat.main import create_app
from jambachat.tools.registry import ToolRegistry
from jambachat.whatsapp.client import WhatsAppClient
from tests.fakes import ScriptedLLM, make_settings


async def test_health_returns_ok() -> None:
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_ready_checks_the_database(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    settings = make_settings()
    async with httpx.AsyncClient() as http:
        whatsapp = WhatsAppClient(http, access_token="t", phone_number_id="1", api_version="v23.0")
        conversation = ConversationService(
            sessionmaker, whatsapp, Orchestrator(ScriptedLLM(), ToolRegistry()), settings
        )
        app = create_app(Container(settings, sessionmaker, conversation))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/health/ready")).json() == {"status": "ok"}

        broken = async_sessionmaker(create_async_engine("sqlite+aiosqlite:////nao/existe/x.db"))
        app = create_app(Container(settings, broken, conversation))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/health/ready")
    assert response.status_code == 503

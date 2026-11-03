from datetime import UTC, datetime, timedelta

import httpx
import respx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jambachat.conversation.commands import (
    CLEARED_TEXT,
    DELETE_CONFIRM_TEXT,
    DELETED_TEXT,
    HELP_TEXT,
)
from jambachat.conversation.history import load_history
from jambachat.conversation.models import Message, Role
from jambachat.conversation.orchestrator import Orchestrator
from jambachat.conversation.service import LLM_DOWN_TEXT, UNSUPPORTED_TEXT, ConversationService
from jambachat.llm.types import LLMError, LLMResponse
from jambachat.tools.registry import ToolRegistry
from jambachat.users.models import User
from jambachat.whatsapp.client import WhatsAppClient
from jambachat.whatsapp.inbound import InboundMessage
from tests.fakes import GRAPH_MESSAGES_URL, ScriptedLLM, graph_ok, make_settings, sent_texts

Maker = async_sessionmaker[AsyncSession]
counter = iter(range(10_000))


def inbound(text: str | None, kind: str = "text") -> InboundMessage:
    return InboundMessage(
        wa_message_id=f"wamid.{next(counter)}",
        phone="5511999990000",
        name="João",
        kind=kind,
        text=text,
        timestamp=datetime.now(UTC) - timedelta(seconds=1),
    )


async def converse(sessionmaker: Maker, llm: ScriptedLLM, *messages: InboundMessage) -> list[str]:
    with respx.mock:
        route = respx.post(GRAPH_MESSAGES_URL).mock(side_effect=graph_ok)
        async with httpx.AsyncClient() as http:
            whatsapp = WhatsAppClient(
                http, access_token="t", phone_number_id="123", api_version="v23.0"
            )
            service = ConversationService(
                sessionmaker, whatsapp, Orchestrator(llm, ToolRegistry()), make_settings()
            )
            for message in messages:
                user_id = await service.register_inbound(message)
                if user_id is not None:
                    await service.process(user_id, message)
        return sent_texts(route)


async def test_conversation_keeps_context_and_persists_turns(sessionmaker: Maker) -> None:
    llm = ScriptedLLM(LLMResponse("Oi!"), LLMResponse("Você disse oi."))
    replies = await converse(sessionmaker, llm, inbound("oi"), inbound("o que eu disse?"))

    assert replies == ["Oi!", "Você disse oi."]
    assert [(m.role, m.content) for m in llm.calls[1][1]] == [
        ("user", "oi"),
        ("assistant", "Oi!"),
        ("user", "o que eu disse?"),
    ]
    async with sessionmaker() as session:
        roles = list(await session.scalars(select(Message.role).order_by(Message.created_at)))
        user = (await session.scalars(select(User))).one()
    assert roles == [Role.USER, Role.ASSISTANT, Role.USER, Role.ASSISTANT]
    assert user.name == "João"
    assert user.last_inbound_at is not None


async def test_duplicate_delivery_is_processed_once(sessionmaker: Maker) -> None:
    message = inbound("oi")
    replies = await converse(sessionmaker, ScriptedLLM(LLMResponse("Oi!")), message, message)

    assert replies == ["Oi!"]


async def test_commands_and_unsupported_messages(sessionmaker: Maker) -> None:
    llm = ScriptedLLM(LLMResponse("Oi!"), LLMResponse("Novo assunto."))
    replies = await converse(
        sessionmaker,
        llm,
        inbound("/ajuda"),
        inbound(None, kind="audio"),
        inbound("oi"),
        inbound("/limpar"),
        inbound("e agora?"),
        inbound("/xyz"),
    )

    assert replies[:2] == [HELP_TEXT, UNSUPPORTED_TEXT]
    assert replies[3] == CLEARED_TEXT
    assert "/ajuda" in replies[5]
    assert [m.content for m in llm.calls[1][1]] == ["e agora?"]


async def test_llm_failure_sends_friendly_message(sessionmaker: Maker) -> None:
    replies = await converse(sessionmaker, ScriptedLLM(LLMError("fora do ar")), inbound("oi"))

    assert replies == [LLM_DOWN_TEXT]


async def test_delete_my_data_requires_confirmation(sessionmaker: Maker) -> None:
    replies = await converse(
        sessionmaker,
        ScriptedLLM(),
        inbound("/apagar-meus-dados"),
        inbound("/apagar-meus-dados confirmar"),
    )

    assert replies == [DELETE_CONFIRM_TEXT, DELETED_TEXT]
    async with sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(User)) == 0
        assert await session.scalar(select(func.count()).select_from(Message)) == 0


async def test_history_expires_and_starts_with_user(sessionmaker: Maker) -> None:
    await converse(sessionmaker, ScriptedLLM(LLMResponse("Oi!")), inbound("oi"))
    async with sessionmaker() as session:
        user = (await session.scalars(select(User))).one()
        now = datetime.now(UTC)
        assert [m.role for m in await load_history(session, user, now)] == ["user", "assistant"]
        assert await load_history(session, user, now + timedelta(hours=7)) == []

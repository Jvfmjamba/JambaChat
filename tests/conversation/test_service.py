import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import respx
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from jambachat.conversation.commands import (
    CLEARED_TEXT,
    DELETE_CONFIRM_TEXT,
    DELETED_TEXT,
    EXPORTED_TEXT,
    HELP_TEXT,
)
from jambachat.conversation.history import MAX_OLD_TOOL_RESULT, load_history, save_turn
from jambachat.conversation.models import Message, Role
from jambachat.conversation.orchestrator import Orchestrator
from jambachat.conversation.service import (
    INTERNAL_ERROR_TEXT,
    LLM_DOWN_TEXT,
    UNSUPPORTED_TEXT,
    WELCOME_TEXT,
    ConversationService,
)
from jambachat.db.models import Base
from jambachat.db.session import tune_sqlite
from jambachat.llm.types import ChatMessage, LLMError, LLMResponse, ToolCall
from jambachat.reminders.models import Reminder, ReminderStatus
from jambachat.tools.registry import Attachment, Tool, ToolContext, ToolRegistry
from jambachat.users.models import User
from jambachat.whatsapp.client import WhatsAppClient
from jambachat.whatsapp.inbound import InboundMessage
from tests.fakes import GRAPH_MESSAGES_URL, ScriptedLLM, graph_ok, make_settings, sent_texts

Maker = async_sessionmaker[AsyncSession]


class NoArgs(BaseModel):
    pass


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


async def test_burst_of_messages_gets_a_single_reply(sessionmaker: Maker) -> None:
    llm = ScriptedLLM(LLMResponse("Respondendo às duas."), LLMResponse("Ajuda?"))
    first, second, command = inbound("oi"), inbound("tudo bem?"), inbound("/ajuda")
    with respx.mock:
        route = respx.post(GRAPH_MESSAGES_URL).mock(side_effect=graph_ok)
        async with httpx.AsyncClient() as http:
            whatsapp = WhatsAppClient(
                http, access_token="t", phone_number_id="123", api_version="v23.0"
            )
            service = ConversationService(
                sessionmaker, whatsapp, Orchestrator(llm, ToolRegistry()), make_settings()
            )
            ids = [await service.register_inbound(m) for m in (first, second, command)]
            for user_id, message in zip(ids, (first, second, command), strict=True):
                assert user_id is not None
                await service.process(user_id, message)

    assert sent_texts(route) == ["Respondendo às duas.", HELP_TEXT]
    assert [m.content for m in llm.calls[0][1]] == ["oi", "tudo bem?"]
    reads = [json.loads(c.request.content) for c in route.calls]
    assert all(r["typing_indicator"] == {"type": "text"} for r in reads if "status" in r)


async def test_commands_do_not_enter_llm_history(sessionmaker: Maker) -> None:
    llm = ScriptedLLM(LLMResponse("Oi!"))
    await converse(sessionmaker, llm, inbound("/ajuda"), inbound("oi"))

    assert [m.content for m in llm.calls[0][1]] == ["oi"]


async def test_reactions_and_stickers_are_ignored(sessionmaker: Maker) -> None:
    replies = await converse(
        sessionmaker, ScriptedLLM(), inbound(None, kind="reaction"), inbound(None, kind="sticker")
    )
    assert replies == []


async def test_location_goes_to_the_model(sessionmaker: Maker) -> None:
    llm = ScriptedLLM(LLMResponse("Tá sol aí!"))
    text = "[localização enviada] (latitude -8.05000, longitude -34.90000)"
    replies = await converse(sessionmaker, llm, inbound(text, kind="location"))

    assert replies == ["Tá sol aí!"]
    assert llm.calls[0][1][0].content == text


async def test_unexpected_error_still_answers(sessionmaker: Maker) -> None:
    replies = await converse(sessionmaker, ScriptedLLM(RuntimeError("bug")), inbound("oi"))
    assert replies == [INTERNAL_ERROR_TEXT]


async def test_attachments_are_sent_after_the_reply(sessionmaker: Maker) -> None:
    async def attach(ctx: ToolContext, args: NoArgs) -> dict[str, object]:
        ctx.attachments.append(Attachment("a.csv", "text/csv", b"x;y", "planilha"))
        return {"ok": True}

    llm = ScriptedLLM(
        LLMResponse("", (ToolCall("c1", "attach", {}),)), LLMResponse("Segue a planilha.")
    )
    with respx.mock:
        messages = respx.post(GRAPH_MESSAGES_URL).mock(side_effect=graph_ok)
        upload = respx.post("https://graph.facebook.com/v23.0/123/media").respond(
            json={"id": "media-1"}
        )
        async with httpx.AsyncClient() as http:
            whatsapp = WhatsAppClient(
                http, access_token="t", phone_number_id="123", api_version="v23.0"
            )
            registry = ToolRegistry([Tool("attach", "anexa", NoArgs, attach)])
            service = ConversationService(
                sessionmaker, whatsapp, Orchestrator(llm, registry), make_settings()
            )
            message = inbound("me manda a planilha")
            user_id = await service.register_inbound(message)
            assert user_id is not None
            await service.process(user_id, message)

    bodies = [json.loads(c.request.content) for c in messages.calls]
    sent = [b for b in bodies if "type" in b]
    assert [b["type"] for b in sent] == ["text", "document"]
    assert sent[1]["document"] == {"id": "media-1", "filename": "a.csv", "caption": "planilha"}
    assert b'filename="a.csv"' in upload.calls[0].request.content


async def test_status_command_is_admin_only(sessionmaker: Maker) -> None:
    replies = await converse(sessionmaker, ScriptedLLM(), inbound("/status"))
    assert "/ajuda" in replies[0]

    async with sessionmaker() as session:
        user = (await session.scalars(select(User))).one()
        user.is_admin = True
        await session.commit()
    replies = await converse(sessionmaker, ScriptedLLM(), inbound("/status"))
    assert replies[0].startswith("*JambaChat")
    assert "Usuários: 1" in replies[0]


async def test_reminder_buttons_skip_the_model(sessionmaker: Maker) -> None:
    await converse(sessionmaker, ScriptedLLM(LLMResponse("Oi!")), inbound("oi"))
    async with sessionmaker() as session:
        user = (await session.scalars(select(User))).one()
        session.add(
            Reminder(
                user_id=user.id,
                text="beber água",
                due_at=datetime.now(UTC),
                status=ReminderStatus.SENT,
                sent_at=datetime.now(UTC),
            )
        )
        await session.commit()

    llm = ScriptedLLM()
    replies = await converse(
        sessionmaker,
        llm,
        inbound("Adiar 10 min", kind="interactive"),
        inbound("Feito ✅", kind="interactive"),
    )

    assert replies[0].startswith("Beleza, te lembro de novo às ")
    assert replies[1] == "Boa! ✅"
    assert llm.calls == []
    async with sessionmaker() as session:
        pending = list(
            await session.scalars(
                select(Reminder.text).where(Reminder.status == ReminderStatus.PENDING)
            )
        )
    assert pending == ["beber água"]


async def test_old_tool_results_are_truncated_in_history(sessionmaker: Maker) -> None:
    await converse(sessionmaker, ScriptedLLM(LLMResponse("Oi!")), inbound("oi"))
    async with sessionmaker() as session:
        user = (await session.scalars(select(User))).one()
        await save_turn(
            session,
            user.id,
            [
                ChatMessage("assistant", "", (ToolCall("c1", "x", {}),)),
                ChatMessage("tool", "a" * 5000, tool_call_id="c1"),
                ChatMessage("assistant", "pronto"),
            ],
        )
        await session.commit()
        history = await load_history(session, user, datetime.now(UTC))

    tool = next(m for m in history if m.role == "tool")
    assert len(tool.content) < MAX_OLD_TOOL_RESULT + 30
    assert tool.content.endswith("[resultado truncado]")


class InterleavingLLM:
    """Simula a chegada de uma mensagem nova enquanto o modelo ainda responde."""

    def __init__(self, service_holder: dict[str, Any], late: InboundMessage) -> None:
        self.holder = service_holder
        self.late = late
        self.calls: list[list[ChatMessage]] = []

    async def complete(
        self, system: str, messages: Sequence[ChatMessage], tools: Sequence[Any]
    ) -> LLMResponse:
        self.calls.append(list(messages))
        if len(self.calls) == 1:
            self.holder["late_user"] = await self.holder["service"].register_inbound(self.late)
            return LLMResponse("Resposta da primeira.")
        return LLMResponse("Resposta da segunda.")


async def test_message_arriving_mid_reply_is_answered_after_it(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'chat.db'}")
    tune_sqlite(engine)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    first, late = inbound("primeira"), inbound("segunda")
    holder: dict[str, Any] = {}
    llm = InterleavingLLM(holder, late)
    with respx.mock:
        route = respx.post(GRAPH_MESSAGES_URL).mock(side_effect=graph_ok)
        async with httpx.AsyncClient() as http:
            whatsapp = WhatsAppClient(
                http, access_token="t", phone_number_id="123", api_version="v23.0"
            )
            service = ConversationService(
                sessionmaker, whatsapp, Orchestrator(llm, ToolRegistry()), make_settings()
            )
            holder["service"] = service
            user_id = await service.register_inbound(first)
            assert user_id is not None
            await service.process(user_id, first)
            await service.process(holder["late_user"], late)

    assert sent_texts(route) == ["Resposta da primeira.", "Resposta da segunda."]
    assert [(m.role, m.content) for m in llm.calls[1]] == [
        ("user", "primeira"),
        ("assistant", "Resposta da primeira."),
        ("user", "segunda"),
    ]
    await engine.dispose()


async def test_llm_failure_drops_attachments(sessionmaker: Maker) -> None:
    async def remember(ctx: ToolContext, args: NoArgs) -> dict[str, object]:
        ctx.user.name = "mudou"
        ctx.attachments.append(Attachment("a.csv", "text/csv", b"x"))
        return {"ok": True}

    llm = ScriptedLLM(LLMResponse("", (ToolCall("c1", "remember", {}),)), LLMError("caiu"))
    registry = ToolRegistry([Tool("remember", "muda", NoArgs, remember)])
    with respx.mock:
        route = respx.post(GRAPH_MESSAGES_URL).mock(side_effect=graph_ok)
        async with httpx.AsyncClient() as http:
            whatsapp = WhatsAppClient(
                http, access_token="t", phone_number_id="123", api_version="v23.0"
            )
            service = ConversationService(
                sessionmaker, whatsapp, Orchestrator(llm, registry), make_settings()
            )
            message = inbound("faz algo")
            user_id = await service.register_inbound(message)
            assert user_id is not None
            await service.process(user_id, message)

    bodies = [json.loads(c.request.content) for c in route.calls]
    assert [b["type"] for b in bodies if "type" in b] == ["text"]
    assert sent_texts(route) == [LLM_DOWN_TEXT]


async def test_user_locks_are_released(sessionmaker: Maker) -> None:
    async with httpx.AsyncClient() as http:
        whatsapp = WhatsAppClient(http, access_token="t", phone_number_id="1", api_version="v1")
        service = ConversationService(
            sessionmaker, whatsapp, Orchestrator(ScriptedLLM(), ToolRegistry()), make_settings()
        )
        user_id = uuid.uuid4()
        async with service._user_lock(user_id):
            assert user_id in service._locks
        assert service._locks == {}


async def test_welcome_on_first_contact_only(sessionmaker: Maker) -> None:
    llm = ScriptedLLM(LLMResponse("Oi!"), LLMResponse("De novo."))
    with respx.mock:
        route = respx.post(GRAPH_MESSAGES_URL).mock(side_effect=graph_ok)
        async with httpx.AsyncClient() as http:
            whatsapp = WhatsAppClient(
                http, access_token="t", phone_number_id="123", api_version="v23.0"
            )
            service = ConversationService(
                sessionmaker,
                whatsapp,
                Orchestrator(llm, ToolRegistry()),
                make_settings(send_welcome=True),
            )
            for message in (inbound("oi"), inbound("e aí")):
                user_id = await service.register_inbound(message)
                assert user_id is not None
                await service.process(user_id, message)

    assert sent_texts(route) == [WELCOME_TEXT, "Oi!", "De novo."]


async def test_welcome_survives_bursts_and_skips_failures(sessionmaker: Maker) -> None:
    llm = ScriptedLLM(LLMError("fora"), LLMResponse("Agora sim."))
    with respx.mock:
        route = respx.post(GRAPH_MESSAGES_URL).mock(side_effect=graph_ok)
        async with httpx.AsyncClient() as http:
            whatsapp = WhatsAppClient(
                http, access_token="t", phone_number_id="123", api_version="v23.0"
            )
            service = ConversationService(
                sessionmaker,
                whatsapp,
                Orchestrator(llm, ToolRegistry()),
                make_settings(send_welcome=True),
            )
            first = inbound("oi")
            user_id = await service.register_inbound(first)
            assert user_id is not None
            await service.process(user_id, first)
            retry, burst = inbound("oi de novo"), inbound("tá aí?")
            await service.register_inbound(retry)
            await service.register_inbound(burst)
            await service.process(user_id, retry)
            await service.process(user_id, burst)

    assert sent_texts(route) == [LLM_DOWN_TEXT, WELCOME_TEXT, "Agora sim."]


async def test_export_my_data_sends_json(sessionmaker: Maker) -> None:
    llm = ScriptedLLM(LLMResponse("Oi!"))
    with respx.mock:
        messages = respx.post(GRAPH_MESSAGES_URL).mock(side_effect=graph_ok)
        upload = respx.post("https://graph.facebook.com/v23.0/123/media").respond(
            json={"id": "media-9"}
        )
        async with httpx.AsyncClient() as http:
            whatsapp = WhatsAppClient(
                http, access_token="t", phone_number_id="123", api_version="v23.0"
            )
            service = ConversationService(
                sessionmaker, whatsapp, Orchestrator(llm, ToolRegistry()), make_settings()
            )
            for message in (inbound("oi"), inbound("/exportar-meus-dados")):
                user_id = await service.register_inbound(message)
                assert user_id is not None
                await service.process(user_id, message)

    assert sent_texts(messages)[-1] == EXPORTED_TEXT
    body = upload.calls[0].request.content
    payload = json.loads(body[body.index(b"{") : body.rindex(b"}") + 1])
    assert payload["profile"]["phone"] == "5511999990000"
    assert [m["text"] for m in payload["messages"]] == ["oi", "Oi!", "/exportar-meus-dados"]

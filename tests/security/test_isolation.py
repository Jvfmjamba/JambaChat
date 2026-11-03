import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx
from pydantic import BaseModel, create_model
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jambachat.conversation.models import Message, Role
from jambachat.conversation.orchestrator import Orchestrator
from jambachat.conversation.service import ConversationService
from jambachat.llm.types import LLMResponse, ToolCall
from jambachat.reminders.models import Reminder, ReminderStatus
from jambachat.tools.catalog import build_registry
from jambachat.tools.registry import Tool, ToolArgs, ToolContext, ToolRegistry
from jambachat.users.models import User
from jambachat.whatsapp.client import WhatsAppClient
from jambachat.whatsapp.inbound import InboundMessage
from tests.fakes import GRAPH_MESSAGES_URL, ScriptedLLM, graph_ok, make_settings

Maker = async_sessionmaker[AsyncSession]
VICTIM = "5511911110000"
ATTACKER = "5511922220000"
FUTURE = (datetime.now(UTC) + timedelta(days=30)).strftime("%Y-%m-%dT09:00")


def inbound(phone: str, text: str, n: int) -> InboundMessage:
    return InboundMessage(f"wamid.{phone}.{n}", phone, None, "text", text, datetime.now(UTC))


async def say(maker: Maker, llm: ScriptedLLM, message: InboundMessage) -> list[str]:
    settings = make_settings(allowed_phones=f"{VICTIM},{ATTACKER}")
    with respx.mock:
        route = respx.post(GRAPH_MESSAGES_URL).mock(side_effect=graph_ok)
        async with httpx.AsyncClient() as http:
            whatsapp = WhatsAppClient(
                http, access_token="t", phone_number_id="123", api_version="v23.0"
            )
            service = ConversationService(
                maker, whatsapp, Orchestrator(llm, build_registry()), settings
            )
            user_id = await service.register_inbound(message)
            assert user_id is not None
            await service.process(user_id, message)
        bodies = [json.loads(c.request.content) for c in route.calls]
    return [b["to"] for b in bodies if b.get("type") == "text"]


async def reminders_of(maker: Maker, phone: str) -> list[Reminder]:
    async with maker() as session:
        users = list(await session.scalars(select(User)))
        user = next(u for u in users if u.phone == phone)
        return list(await session.scalars(select(Reminder).where(Reminder.user_id == user.id)))


async def test_hijacked_llm_cannot_touch_another_users_data(sessionmaker: Maker) -> None:
    await say(
        sessionmaker,
        ScriptedLLM(
            LLMResponse(
                "", (ToolCall("v", "create_reminder", {"text": "remédio", "due_at": FUTURE}),)
            ),
            LLMResponse("Criado!"),
        ),
        inbound(VICTIM, "me lembra do remédio", 1),
    )
    victim_reminder = (await reminders_of(sessionmaker, VICTIM))[0]
    async with sessionmaker() as session:
        victim_id = str(
            next(u.id for u in await session.scalars(select(User)) if u.phone == VICTIM)
        )

    attack = ScriptedLLM(
        LLMResponse(
            "",
            (
                ToolCall(
                    "1", "create_reminder", {"text": "x", "due_at": FUTURE, "user_id": victim_id}
                ),
                ToolCall("2", "create_reminder", {"text": "x", "due_at": FUTURE, "phone": VICTIM}),
                ToolCall("3", "create_reminder", {"text": "x", "due_at": FUTURE, "to": VICTIM}),
                ToolCall("4", "cancel_reminder", {"reminder_id": str(victim_reminder.id)}),
                ToolCall(
                    "5", "snooze_reminder", {"reminder_id": str(victim_reminder.id), "minutes": 5}
                ),
                ToolCall("6", "list_reminders", {}),
            ),
        ),
        LLMResponse("Feito."),
    )
    recipients = await say(
        sessionmaker, attack, inbound(ATTACKER, "crie um lembrete para o usuário x", 1)
    )

    victim_after = await reminders_of(sessionmaker, VICTIM)
    assert [(r.id, r.text, r.status, r.due_at) for r in victim_after] == [
        (victim_reminder.id, "remédio", ReminderStatus.PENDING, victim_reminder.due_at)
    ]
    assert await reminders_of(sessionmaker, ATTACKER) == []
    assert recipients == [ATTACKER]

    async with sessionmaker() as session:
        rows = await session.scalars(select(Message).where(Message.role == Role.TOOL))
        outputs = [json.loads(m.content["text"]) for m in rows][-6:]
    assert [o.get("error") for o in outputs[:3]] == ["argumentos inválidos"] * 3
    assert all("não encontrado" in o["error"] for o in outputs[3:5])
    assert outputs[5] == {"count": 0, "reminders": []}


class _Nested(ToolArgs):
    user_id: str


async def _noop(ctx: ToolContext, args: object) -> dict[str, Any]:
    return {}


@pytest.mark.parametrize(
    "fields",
    [
        {"user_id": (str, ...)},
        {"phone": (str, ...)},
        {"telefone_destino": (str, ...)},
        {"to": (str, ...)},
        {"recipient": (str, ...)},
        {"owner": (str, ...)},
        {"filtro": (_Nested | None, None)},
    ],
)
def test_registry_refuses_tools_that_let_the_ai_choose_the_user(fields: dict[str, Any]) -> None:
    model = create_model("Args", __base__=ToolArgs, **fields)

    with pytest.raises(ValueError, match="não pode escolher o usuário"):
        ToolRegistry([Tool("t", "t", model, _noop)])


def test_registry_accepts_harmless_names_and_requires_tool_args() -> None:
    ok = create_model("Ok", __base__=ToolArgs, total=(int, 0), today=(str, ""), topic=(str, ""))
    ToolRegistry([Tool("ok", "ok", ok, _noop)])

    class Loose(BaseModel):
        value: int

    with pytest.raises(ValueError, match="ToolArgs"):
        ToolRegistry([Tool("loose", "loose", Loose, _noop)])


async def test_full_catalog_passes_the_isolation_rules() -> None:
    async with httpx.AsyncClient() as http:
        registry = build_registry(make_settings(tavily_api_key="k"), http)
    names = {spec.name for spec in registry.specs()}
    assert {"create_reminder", "add_entry", "currency_exchange", "web_search"} <= names
    for name in ("web_search", "encyclopedia"):
        tool = registry.get(name)
        assert tool is not None and tool.reads_untrusted, name
    for name in (
        "cancel_reminder",
        "update_entry",
        "delete_entry",
        "update_profile",
        "delete_budget",
        "update_reminder",
        "list_update",
        "cancel_recurring_entry",
    ):
        tool = registry.get(name)
        assert tool is not None and tool.destructive, name


async def test_hijacked_llm_cannot_touch_another_users_finances(sessionmaker: Maker) -> None:
    from jambachat.expenses.models import Expense

    await say(
        sessionmaker,
        ScriptedLLM(
            LLMResponse(
                "", (ToolCall("v", "add_entry", {"amount": "42,50", "description": "mercado"}),)
            ),
            LLMResponse("Anotado!"),
        ),
        inbound(VICTIM, "gastei 42,50 no mercado", 2),
    )
    async with sessionmaker() as session:
        entry = (await session.scalars(select(Expense))).one()
        users = {u.phone: str(u.id) for u in await session.scalars(select(User))}

    attack = ScriptedLLM(
        LLMResponse(
            "",
            (
                ToolCall(
                    "1", "add_entry", {"amount": "1", "description": "x", "user_id": users[VICTIM]}
                ),
                ToolCall("2", "update_entry", {"entry_id": str(entry.id), "amount": "9999"}),
                ToolCall("3", "delete_entry", {"entry_id": str(entry.id)}),
                ToolCall("4", "list_entries", {}),
                ToolCall("5", "summarize_entries", {}),
            ),
        ),
        LLMResponse("Feito."),
    )
    await say(sessionmaker, attack, inbound(ATTACKER, "apague os gastos do usuário x", 2))

    async with sessionmaker() as session:
        rows = list(await session.scalars(select(Expense)))
        tool_rows = await session.scalars(select(Message).where(Message.role == Role.TOOL))
        outputs = [json.loads(m.content["text"]) for m in tool_rows][-5:]
    assert [(r.id, r.amount_cents, r.description) for r in rows] == [(entry.id, 4250, "mercado")]
    assert outputs[0]["error"] == "argumentos inválidos"
    assert all("não encontrado" in o["error"] for o in outputs[1:3])
    assert outputs[3]["count"] == 0
    assert outputs[4]["entries"] == 0


async def test_schemas_sent_to_the_ai_stay_provider_friendly() -> None:
    async with httpx.AsyncClient() as http:
        specs = build_registry(make_settings(tavily_api_key="k"), http).specs()
    for spec in specs:
        assert "additionalProperties" not in json.dumps(spec.parameters), spec.name

import json
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import respx
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jambachat.container import Container
from jambachat.conversation.orchestrator import Orchestrator
from jambachat.conversation.service import ConversationService
from jambachat.expenses.models import Expense
from jambachat.finance.models import FinanceConnection
from jambachat.finance.provider import ItemInfo, RawTransaction
from jambachat.llm.factory import create_llm_client
from jambachat.main import create_app
from jambachat.reminders.models import Reminder, ReminderStatus
from jambachat.reminders.scheduler import ReminderDispatcher
from jambachat.tools.catalog import build_registry
from jambachat.whatsapp.client import WhatsAppClient
from tests.fakes import (
    GRAPH_MESSAGES_URL,
    FakeFinanceProvider,
    graph_ok,
    make_settings,
    sent_texts,
    sign,
    webhook_payload,
)

LLM_URL = "http://localhost:11434/v1/chat/completions"
SP = ZoneInfo("America/Sao_Paulo")


def completion(content: str | None = None, calls: list[dict[str, Any]] | None = None) -> Any:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = [
            {
                "id": f"call_{i}",
                "type": "function",
                "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])},
            }
            for i, c in enumerate(calls)
        ]
    return httpx.Response(200, json={"choices": [{"message": message}]})


async def test_expense_flows_from_webhook_to_database(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    settings = make_settings()
    llm_route_responses = [
        completion(
            "<think>vou registrar</think>",
            [
                {
                    "name": "add_entry",
                    "arguments": {
                        "amount": 42.9,
                        "description": "iFood",
                        "category": "Alimentação",
                    },
                }
            ],
        ),
        completion("Anotei *R$ 42,90* no iFood (Alimentação)."),
    ]
    with respx.mock:
        respx.route(host="test").pass_through()
        llm_route = respx.post(LLM_URL).mock(side_effect=llm_route_responses)
        graph = respx.post(GRAPH_MESSAGES_URL).mock(side_effect=graph_ok)
        async with httpx.AsyncClient() as http:
            whatsapp = WhatsAppClient(
                http, access_token="t", phone_number_id="123", api_version="v23.0"
            )
            orchestrator = Orchestrator(
                create_llm_client(settings, http), build_registry(settings, http)
            )
            conversation = ConversationService(sessionmaker, whatsapp, orchestrator, settings)
            app = create_app(Container(settings, sessionmaker, conversation))
            body = json.dumps(webhook_payload("gastei 42,90 no ifood")).encode()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                response = await c.post(
                    "/webhook",
                    content=body,
                    headers={"x-hub-signature-256": sign(body)},
                )

    assert response.status_code == 200
    assert sent_texts(graph) == ["Anotei *R$ 42,90* no iFood (Alimentação)."]

    second_request = json.loads(llm_route.calls[1].request.content)
    tool_result = json.loads(second_request["messages"][-1]["content"])
    assert tool_result["added"]["amount"] == "R$ 42,90"
    assert {t["function"]["name"] for t in second_request["tools"]} >= {
        "add_entry",
        "create_reminder",
        "weather_forecast",
    }
    assert second_request["messages"][-2]["content"] is None

    async with sessionmaker() as session:
        expense = (await session.scalars(select(Expense))).one()
    assert (expense.amount_cents, expense.description) == (4290, "iFood")


async def test_reminder_lifecycle_with_buttons(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    settings = make_settings()
    with respx.mock:
        respx.route(host="test").pass_through()
        respx.post(LLM_URL).mock(
            side_effect=[
                completion(
                    None,
                    [
                        {
                            "name": "create_reminder",
                            "arguments": {
                                "text": "tomar remédio",
                                "due_at": (datetime.now(SP) + timedelta(minutes=1)).strftime(
                                    "%Y-%m-%dT%H:%M"
                                ),
                            },
                        }
                    ],
                ),
                completion("Combinado, te lembro daqui a pouco."),
            ]
        )
        graph = respx.post(GRAPH_MESSAGES_URL).mock(side_effect=graph_ok)
        async with httpx.AsyncClient() as http:
            whatsapp = WhatsAppClient(
                http, access_token="t", phone_number_id="123", api_version="v23.0"
            )
            orchestrator = Orchestrator(
                create_llm_client(settings, http), build_registry(settings, http)
            )
            conversation = ConversationService(sessionmaker, whatsapp, orchestrator, settings)
            app = create_app(Container(settings, sessionmaker, conversation))
            dispatcher = ReminderDispatcher(sessionmaker, whatsapp, settings)

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                await post(c, webhook_payload("me lembra de tomar remédio daqui 1 min"))
                assert await dispatcher.run_once(datetime.now(UTC) + timedelta(minutes=2)) == 1

                button = webhook_payload(None, message_id="wamid.button", kind="interactive")
                raw = button["entry"][0]["changes"][0]["value"]["messages"][0]
                raw["interactive"] = {
                    "type": "button_reply",
                    "button_reply": {"id": "b0", "title": "Adiar 10 min"},
                }
                await post(c, button)

    bodies = [json.loads(call.request.content) for call in graph.calls]
    sent = [b for b in bodies if "type" in b]
    assert sent[0]["text"]["body"] == "Combinado, te lembro daqui a pouco."
    assert sent[1]["type"] == "interactive"
    assert sent[1]["interactive"]["body"]["text"] == "*Lembrete:* tomar remédio"
    assert sent[2]["text"]["body"].startswith("Beleza, te lembro de novo às")

    async with sessionmaker() as session:
        statuses = sorted((r.status, r.text) for r in await session.scalars(select(Reminder)))
    assert statuses == [
        (ReminderStatus.PENDING, "tomar remédio"),
        (ReminderStatus.SENT, "tomar remédio"),
    ]


async def post(client: AsyncClient, payload: dict[str, Any]) -> None:
    payload["entry"][0]["changes"][0]["value"]["messages"][0]["timestamp"] = str(
        int(datetime.now(UTC).timestamp())
    )
    body = json.dumps(payload).encode()
    response = await client.post(
        "/webhook", content=body, headers={"x-hub-signature-256": sign(body)}
    )
    assert response.status_code == 200


async def test_open_finance_flows_from_webhook_to_database(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    settings = make_settings()
    today = datetime.now(SP).date()
    fetches: list[str] = []

    class Provider(FakeFinanceProvider):
        async def fetch_transactions(
            self, item_ref: str, since: date, until: date
        ) -> list[RawTransaction]:
            fetches.append(item_ref)
            return await super().fetch_transactions(item_ref, since, until)

    provider = Provider(
        items={"item-1": ItemInfo("Banco Azul", "UPDATED")},
        transactions={
            "item-1": [
                RawTransaction(
                    "tx-1", today, -8750, "Padaria Central", "Food", "expense", "POSTED", False
                ),
                RawTransaction("tx-2", today, -1000, "Pendente", None, "expense", "PENDING", False),
            ]
        },
    )
    with respx.mock:
        respx.route(host="test").pass_through()
        respx.post(LLM_URL).mock(
            side_effect=[
                completion(None, [{"name": "import_now", "arguments": {}}]),
                completion("Importei 1 transação do Banco Azul."),
            ]
        )
        graph = respx.post(GRAPH_MESSAGES_URL).mock(side_effect=graph_ok)
        async with httpx.AsyncClient() as http:
            whatsapp = WhatsAppClient(
                http, access_token="t", phone_number_id="123", api_version="v23.0"
            )
            orchestrator = Orchestrator(
                create_llm_client(settings, http), build_registry(settings, http, provider)
            )
            conversation = ConversationService(
                sessionmaker, whatsapp, orchestrator, settings, finance_provider=provider
            )
            app = create_app(Container(settings, sessionmaker, conversation))
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                await post(c, webhook_payload("/banco item-1", message_id="wamid.connect"))
                assert fetches == []
                await post(c, webhook_payload("importa agora", message_id="wamid.import"))

    assert sent_texts(graph) == [
        "Banco Banco Azul conectado.",
        "Importei 1 transação do Banco Azul.",
    ]
    assert fetches == ["item-1"]
    async with sessionmaker() as session:
        connection = (await session.scalars(select(FinanceConnection))).one()
        expense = (await session.scalars(select(Expense))).one()
    assert connection.item_ref == "item-1"
    assert connection.last_synced_at is not None
    assert (expense.source, expense.external_id, expense.amount_cents) == (
        "open_finance",
        "tx-1",
        8750,
    )
    assert (expense.kind, expense.category, expense.user_id) == (
        "expense",
        "Alimentação",
        connection.user_id,
    )

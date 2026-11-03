import json
from typing import Any

import httpx
import respx
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jambachat.container import Container
from jambachat.conversation.orchestrator import Orchestrator
from jambachat.conversation.service import ConversationService
from jambachat.expenses.models import Expense
from jambachat.llm.factory import create_llm_client
from jambachat.main import create_app
from jambachat.tools.catalog import build_registry
from jambachat.whatsapp.client import WhatsAppClient
from tests.fakes import (
    GRAPH_MESSAGES_URL,
    graph_ok,
    make_settings,
    sent_texts,
    sign,
    webhook_payload,
)

LLM_URL = "http://localhost:11434/v1/chat/completions"


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

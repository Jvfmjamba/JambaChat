import json
from typing import Any

import httpx
import respx
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jambachat.container import Container
from jambachat.conversation.models import Message
from jambachat.conversation.orchestrator import Orchestrator
from jambachat.conversation.service import ConversationService
from jambachat.llm.types import LLMResponse
from jambachat.main import create_app
from jambachat.tools.registry import ToolRegistry
from jambachat.whatsapp.client import WhatsAppClient
from tests.fakes import (
    GRAPH_MESSAGES_URL,
    VERIFY_TOKEN,
    ScriptedLLM,
    graph_ok,
    make_settings,
    sent_texts,
    sign,
    webhook_payload,
)

Maker = async_sessionmaker[AsyncSession]


async def call(
    sessionmaker: Maker, llm: ScriptedLLM, method: str, **kwargs: Any
) -> tuple[httpx.Response, list[str]]:
    settings = make_settings()
    with respx.mock:
        respx.route(host="test").pass_through()
        route = respx.post(GRAPH_MESSAGES_URL).mock(side_effect=graph_ok)
        async with httpx.AsyncClient() as http:
            whatsapp = WhatsAppClient(
                http, access_token="t", phone_number_id="123", api_version="v23.0"
            )
            conversation = ConversationService(
                sessionmaker, whatsapp, Orchestrator(llm, ToolRegistry()), settings
            )
            app = create_app(Container(settings, sessionmaker, conversation))
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                response = await c.request(method, "/webhook", **kwargs)
        return response, sent_texts(route)


def signed_post(payload: object, signature: str | None = None) -> dict[str, Any]:
    body = json.dumps(payload).encode()
    headers = {"content-type": "application/json"}
    headers["x-hub-signature-256"] = signature if signature is not None else sign(body)
    return {"content": body, "headers": headers}


async def count_messages(sessionmaker: Maker) -> int:
    async with sessionmaker() as session:
        return int(await session.scalar(select(func.count()).select_from(Message)) or 0)


async def test_verification_handshake(sessionmaker: Maker) -> None:
    params = {
        "hub.mode": "subscribe",
        "hub.verify_token": VERIFY_TOKEN,
        "hub.challenge": "1158201444",
    }
    ok, _ = await call(sessionmaker, ScriptedLLM(), "GET", params=params)
    bad, _ = await call(
        sessionmaker, ScriptedLLM(), "GET", params={**params, "hub.verify_token": "x"}
    )
    xss, _ = await call(
        sessionmaker, ScriptedLLM(), "GET", params={**params, "hub.challenge": "<b>"}
    )

    assert ok.status_code == 200
    assert ok.text == "1158201444"
    assert bad.status_code == 403
    assert xss.status_code == 403


async def test_authorized_message_is_answered(sessionmaker: Maker) -> None:
    response, replies = await call(
        sessionmaker, ScriptedLLM(LLMResponse("Oi!")), "POST", **signed_post(webhook_payload("oi"))
    )

    assert response.status_code == 200
    assert replies == ["Oi!"]
    assert await count_messages(sessionmaker) == 2


async def test_invalid_signature_is_rejected(sessionmaker: Maker) -> None:
    response, replies = await call(
        sessionmaker, ScriptedLLM(), "POST", **signed_post(webhook_payload("oi"), "sha256=00")
    )

    assert response.status_code == 401
    assert replies == []
    assert await count_messages(sessionmaker) == 0


async def test_unauthorized_sender_is_silently_ignored(sessionmaker: Maker) -> None:
    payload = webhook_payload("oi", phone="5599888887777")
    response, replies = await call(sessionmaker, ScriptedLLM(), "POST", **signed_post(payload))

    assert response.status_code == 200
    assert replies == []
    assert await count_messages(sessionmaker) == 0


async def test_oversized_and_malformed_bodies(sessionmaker: Maker) -> None:
    big = b"x" * 1_000_001
    too_big, _ = await call(
        sessionmaker, ScriptedLLM(), "POST", content=big, headers={"x-hub-signature-256": sign(big)}
    )
    junk = b"{nope"
    malformed, _ = await call(
        sessionmaker,
        ScriptedLLM(),
        "POST",
        content=junk,
        headers={"x-hub-signature-256": sign(junk)},
    )

    assert too_big.status_code == 413
    assert malformed.status_code == 400

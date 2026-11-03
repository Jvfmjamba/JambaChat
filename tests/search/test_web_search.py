import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jambachat.llm.types import ToolCall
from jambachat.search.tavily import TAVILY_URL
from jambachat.tools.catalog import build_registry
from jambachat.tools.registry import ToolContext
from jambachat.users.models import User
from tests.fakes import make_settings


@pytest.fixture
async def ctx(sessionmaker: async_sessionmaker[AsyncSession]) -> AsyncIterator[ToolContext]:
    async with sessionmaker() as session:
        user = User(phone="1", phone_hash="1")
        session.add(user)
        await session.flush()
        yield ToolContext(session, user, make_settings(), datetime(2026, 11, 3, tzinfo=UTC))


async def search(ctx: ToolContext, **args: Any) -> dict[str, Any]:
    async with httpx.AsyncClient() as http:
        registry = build_registry(make_settings(tavily_api_key="tvly-teste"), http)
        result: dict[str, Any] = json.loads(
            await registry.execute(ToolCall("c", "web_search", args), ctx)
        )
    return result


@respx.mock
async def test_web_search_returns_answer_and_sources(ctx: ToolContext) -> None:
    route = respx.post(TAVILY_URL).respond(
        json={
            "answer": "O Flamengo venceu por 2 a 1.",
            "results": [
                {
                    "title": "Flamengo vence",
                    "url": "https://ge.globo.com/x",
                    "content": "texto   com\n espaços " + "a" * 1000,
                    "published_date": "2026-11-02",
                },
                {"title": "sem url"},
            ],
        }
    )
    result = await search(ctx, query="quem ganhou o jogo do flamengo", topic="news", days=2)

    sent = json.loads(route.calls[0].request.content)
    assert route.calls[0].request.headers["authorization"] == "Bearer tvly-teste"
    assert (sent["topic"], sent["days"], sent["include_answer"]) == ("news", 2, True)
    assert result["answer"] == "O Flamengo venceu por 2 a 1."
    assert len(result["results"]) == 1
    assert result["results"][0]["snippet"].startswith("texto com espaços a")
    assert len(result["results"][0]["snippet"]) == 600
    assert result["results"][0]["published"] == "2026-11-02"


@respx.mock
async def test_web_search_failure_is_reported(ctx: ToolContext) -> None:
    respx.post(TAVILY_URL).respond(401, json={"detail": "bad key"})
    result = await search(ctx, query="qualquer coisa")
    assert "indisponível" in result["error"]

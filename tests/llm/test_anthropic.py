import json

import httpx
import respx

from jambachat.llm.anthropic import API_VERSION, AnthropicClient
from jambachat.llm.types import ChatMessage, ToolCall, ToolSpec

URL = "https://api.anthropic.com/v1/messages"


@respx.mock
async def test_parses_text_and_tool_use_and_merges_tool_results() -> None:
    route = respx.post(URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "content": [
                    {"type": "text", "text": "Vou registrar."},
                    {
                        "type": "tool_use",
                        "id": "t9",
                        "name": "add_expense",
                        "input": {"amount": 30},
                    },
                ]
            },
        )
    )
    history = [
        ChatMessage("user", "gastei 30 e 12"),
        ChatMessage(
            "assistant",
            tool_calls=(ToolCall("t1", "add_expense", {}), ToolCall("t2", "add_expense", {})),
        ),
        ChatMessage("tool", "ok", tool_call_id="t1"),
        ChatMessage("tool", "ok", tool_call_id="t2"),
    ]
    async with httpx.AsyncClient() as http:
        client = AnthropicClient(
            http, base_url="", api_key="k", model="m", max_tokens=100, timeout=5
        )
        result = await client.complete(
            "sys", history, [ToolSpec("add_expense", "d", {"type": "object"})]
        )

    assert result.text == "Vou registrar."
    assert result.tool_calls == (ToolCall("t9", "add_expense", {"amount": 30}),)

    request = route.calls.last.request
    body = json.loads(request.content)
    assert request.headers["x-api-key"] == "k"
    assert request.headers["anthropic-version"] == API_VERSION
    assert body["system"] == "sys"
    assert body["tools"][0]["input_schema"] == {"type": "object"}
    assert [m["role"] for m in body["messages"]] == ["user", "assistant", "user"]
    assert [b["tool_use_id"] for b in body["messages"][2]["content"]] == ["t1", "t2"]

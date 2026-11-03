import json

import httpx
import pytest
import respx

from jambachat.llm.openai_compatible import OpenAICompatibleClient, strip_reasoning
from jambachat.llm.types import ChatMessage, LLMError, ToolCall, ToolSpec

URL = "http://localhost:11434/v1/chat/completions"
TOOL = ToolSpec("add_expense", "Registra um gasto", {"type": "object", "properties": {}})


def make_client(http: httpx.AsyncClient, api_key: str = "") -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        http,
        base_url="http://localhost:11434/v1/",
        api_key=api_key,
        model="qwen3:8b",
        max_tokens=256,
        timeout=5,
    )


def reply(message: dict[str, object]) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": message}]})


@respx.mock
async def test_text_reply_without_auth_header() -> None:
    route = respx.post(URL).mock(return_value=reply({"content": " oi! "}))
    async with httpx.AsyncClient() as http:
        result = await make_client(http).complete("sys", [ChatMessage("user", "oi")], [])

    assert result.text == "oi!"
    assert result.tool_calls == ()
    request = route.calls.last.request
    assert "authorization" not in request.headers
    body = json.loads(request.content)
    assert body["messages"][0] == {"role": "system", "content": "sys"}
    assert "tools" not in body


@respx.mock
@pytest.mark.parametrize("arguments", ['{"amount": 42}', {"amount": 42}])
async def test_tool_call_arguments_as_string_or_object(arguments: object) -> None:
    respx.post(URL).mock(
        return_value=reply(
            {
                "content": None,
                "tool_calls": [
                    {"id": "c1", "function": {"name": "add_expense", "arguments": arguments}}
                ],
            }
        )
    )
    async with httpx.AsyncClient() as http:
        result = await make_client(http, "k").complete("sys", [ChatMessage("user", "x")], [TOOL])

    assert result.tool_calls == (ToolCall("c1", "add_expense", {"amount": 42}),)


@respx.mock
async def test_tool_round_trip_is_serialized() -> None:
    route = respx.post(URL).mock(return_value=reply({"content": "feito"}))
    history = [
        ChatMessage("user", "gastei 42"),
        ChatMessage("assistant", tool_calls=(ToolCall("c1", "add_expense", {"amount": 42}),)),
        ChatMessage("tool", '{"ok": true}', tool_call_id="c1"),
    ]
    async with httpx.AsyncClient() as http:
        await make_client(http, "segredo").complete("sys", history, [TOOL])

    request = route.calls.last.request
    body = json.loads(request.content)
    assert request.headers["authorization"] == "Bearer segredo"
    assert body["tools"][0]["function"]["name"] == "add_expense"
    assert body["messages"][2]["tool_calls"][0]["function"]["arguments"] == '{"amount": 42}'
    assert body["messages"][3] == {"role": "tool", "tool_call_id": "c1", "content": '{"ok": true}'}


@respx.mock
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(429, text="limite"),
        httpx.Response(200, text="nao e json"),
        httpx.Response(200, json={"choices": []}),
        reply({"tool_calls": [{"function": {"name": "x", "arguments": "{quebrado"}}]}),
    ],
)
async def test_provider_errors_raise_llm_error(response: httpx.Response) -> None:
    respx.post(URL).mock(return_value=response)
    async with httpx.AsyncClient() as http:
        with pytest.raises(LLMError):
            await make_client(http).complete("sys", [ChatMessage("user", "x")], [])


@respx.mock
async def test_network_failure_raises_llm_error() -> None:
    respx.post(URL).mock(side_effect=httpx.ConnectError("off"))
    async with httpx.AsyncClient() as http:
        with pytest.raises(LLMError):
            await make_client(http).complete("sys", [ChatMessage("user", "x")], [])


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("<think>hmm, vejamos</think>\n\nOi!", "Oi!"),
        ("<THINK>a\nb</THINK>Resposta", "Resposta"),
        ("<think>sem fim", ""),
        ("Sem raciocínio", "Sem raciocínio"),
    ],
)
def test_strip_reasoning(raw: str, clean: str) -> None:
    assert strip_reasoning(raw) == clean

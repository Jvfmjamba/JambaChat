from collections.abc import Sequence
from typing import Any

import httpx

from jambachat.llm.http import post_json
from jambachat.llm.types import ChatMessage, LLMError, LLMResponse, ToolCall, ToolSpec

API_VERSION = "2023-06-01"
DEFAULT_BASE_URL = "https://api.anthropic.com"


def _blocks(message: ChatMessage) -> tuple[str, list[dict[str, Any]]]:
    if message.role == "tool":
        return "user", [
            {"type": "tool_result", "tool_use_id": message.tool_call_id, "content": message.content}
        ]
    blocks: list[dict[str, Any]] = []
    if message.content:
        blocks.append({"type": "text", "text": message.content})
    blocks.extend(
        {"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments}
        for c in message.tool_calls
    )
    return message.role, blocks


def _to_wire(messages: Sequence[ChatMessage]) -> list[dict[str, Any]]:
    wire: list[dict[str, Any]] = []
    for message in messages:
        role, blocks = _blocks(message)
        if wire and wire[-1]["role"] == role:
            wire[-1]["content"].extend(blocks)
        else:
            wire.append({"role": role, "content": blocks})
    return wire


class AnthropicClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        base_url: str,
        api_key: str,
        model: str,
        max_tokens: int,
        timeout: float,
    ) -> None:
        self._http = http
        self._url = (base_url or DEFAULT_BASE_URL).rstrip("/") + "/v1/messages"
        self._api_key = api_key
        self._model = model
        self._max_tokens = max_tokens
        self._timeout = timeout

    async def complete(
        self, system: str, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "system": system,
            "messages": _to_wire(messages),
        }
        if tools:
            payload["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters}
                for t in tools
            ]
        headers = {"x-api-key": self._api_key, "anthropic-version": API_VERSION}
        data = await post_json(self._http, self._url, payload, headers, self._timeout)

        try:
            content = data["content"]
            text = "".join(b["text"] for b in content if b["type"] == "text").strip()
            calls = tuple(
                ToolCall(id=b["id"], name=b["name"], arguments=dict(b.get("input") or {}))
                for b in content
                if b["type"] == "tool_use"
            )
        except (KeyError, TypeError) as exc:
            raise LLMError("formato de resposta inesperado do provedor") from exc
        return LLMResponse(text=text, tool_calls=calls)

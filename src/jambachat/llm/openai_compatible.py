import json
import re
from collections.abc import Sequence
from typing import Any

import httpx

from jambachat.llm.http import post_json
from jambachat.llm.types import ChatMessage, LLMError, LLMResponse, ToolCall, ToolSpec

THINK_BLOCK = re.compile(r"<think>.*?(</think>|$)", re.DOTALL | re.IGNORECASE)


def strip_reasoning(text: str) -> str:
    """Remove o raciocínio que modelos como Qwen3 e DeepSeek-R1 escrevem em <think>."""
    return THINK_BLOCK.sub("", text).strip()


def _to_wire(message: ChatMessage) -> dict[str, Any]:
    if message.role == "tool":
        return {"role": "tool", "tool_call_id": message.tool_call_id, "content": message.content}
    if message.images:
        parts: list[dict[str, Any]] = [
            {
                "type": "image_url",
                "image_url": {"url": f"data:{image.mime_type};base64,{image.base64()}"},
            }
            for image in message.images
        ]
        if message.content:
            parts.append({"type": "text", "text": message.content})
        return {"role": message.role, "content": parts}
    wire: dict[str, Any] = {"role": message.role, "content": message.content or None}
    if message.tool_calls:
        wire["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, ensure_ascii=False),
                },
            }
            for call in message.tool_calls
        ]
    return wire


def _parse_arguments(raw: object) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if raw is None or raw == "":
        return {}
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LLMError("o modelo gerou argumentos de ferramenta inválidos") from exc
        if isinstance(parsed, dict):
            return parsed
    raise LLMError("o modelo gerou argumentos de ferramenta inválidos")


class OpenAICompatibleClient:
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
        self._url = base_url.rstrip("/") + "/chat/completions"
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
            "messages": [{"role": "system", "content": system}, *map(_to_wire, messages)],
        }
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        data = await post_json(self._http, self._url, payload, headers, self._timeout)

        try:
            message = data["choices"][0]["message"]
            raw_calls = message.get("tool_calls") or []
            calls = tuple(
                ToolCall(
                    id=call.get("id") or f"call_{index}",
                    name=call["function"]["name"],
                    arguments=_parse_arguments(call["function"].get("arguments")),
                )
                for index, call in enumerate(raw_calls)
            )
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            raise LLMError("formato de resposta inesperado do provedor") from exc
        return LLMResponse(text=strip_reasoning(message.get("content") or ""), tool_calls=calls)

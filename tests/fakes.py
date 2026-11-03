import hashlib
import hmac
import json
from collections.abc import Sequence
from typing import Any

import httpx

from jambachat.config import Settings
from jambachat.llm.types import ChatMessage, LLMResponse, ToolSpec

PHONE = "5511999990000"
APP_SECRET = "segredo-do-app"
VERIFY_TOKEN = "token-de-verificacao"
GRAPH_MESSAGES_URL = "https://graph.facebook.com/v23.0/123/messages"


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "whatsapp_app_secret": APP_SECRET,
        "whatsapp_verify_token": VERIFY_TOKEN,
        "whatsapp_access_token": "token-de-acesso",
        "whatsapp_phone_number_id": "123",
        "allowed_phones": PHONE,
        "llm_base_url": "http://localhost:11434/v1",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


class ScriptedLLM:
    def __init__(self, *responses: LLMResponse | Exception) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, list[ChatMessage], list[ToolSpec]]] = []

    async def complete(
        self, system: str, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]
    ) -> LLMResponse:
        self.calls.append((system, list(messages), list(tools)))
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def webhook_payload(
    text: str | None = "oi", *, phone: str = PHONE, message_id: str = "wamid.1", kind: str = "text"
) -> dict[str, Any]:
    message: dict[str, Any] = {
        "from": phone,
        "id": message_id,
        "timestamp": "1762171200",
        "type": kind,
    }
    if kind == "text":
        message["text"] = {"body": text}
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "contacts": [{"wa_id": phone, "profile": {"name": "João"}}],
                            "messages": [message],
                        }
                    }
                ]
            }
        ],
    }


def sign(body: bytes, secret: str = APP_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def graph_ok(request: httpx.Request) -> httpx.Response:
    payload = json.loads(request.content)
    if payload.get("status") == "read":
        return httpx.Response(200, json={"success": True})
    return httpx.Response(200, json={"messages": [{"id": "wamid.out"}]})


def sent_texts(route: Any) -> list[str]:
    bodies = [json.loads(call.request.content) for call in route.calls]
    return [b["text"]["body"] for b in bodies if b.get("type") == "text"]

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class InboundMessage:
    wa_message_id: str
    phone: str
    name: str | None
    kind: str
    text: str | None
    timestamp: datetime


def _text_of(message: dict[str, Any], kind: str) -> str | None:
    if kind == "text":
        return str(message["text"]["body"])
    if kind == "button":
        return str(message["button"]["text"])
    if kind == "interactive":
        interactive = message["interactive"]
        reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
        title = reply.get("title")
        return str(title) if title else None
    return None


def parse_webhook(payload: Any) -> list[InboundMessage]:
    if not isinstance(payload, dict) or payload.get("object") != "whatsapp_business_account":
        return []

    messages: list[InboundMessage] = []
    for entry in payload.get("entry") or []:
        for change in (entry or {}).get("changes") or []:
            value = (change or {}).get("value") or {}
            names = {
                c.get("wa_id"): (c.get("profile") or {}).get("name")
                for c in value.get("contacts") or []
            }
            for raw in value.get("messages") or []:
                try:
                    kind = str(raw.get("type", "unknown"))
                    phone = str(raw["from"])
                    messages.append(
                        InboundMessage(
                            wa_message_id=str(raw["id"]),
                            phone=phone,
                            name=names.get(phone),
                            kind=kind,
                            text=_text_of(raw, kind),
                            timestamp=datetime.fromtimestamp(int(raw["timestamp"]), UTC),
                        )
                    )
                except (KeyError, TypeError, ValueError, AttributeError):
                    continue
    return messages

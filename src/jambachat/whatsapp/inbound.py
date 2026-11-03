from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

MEDIA_KINDS = frozenset({"audio", "image"})
IGNORED_KINDS = frozenset({"reaction", "sticker", "system", "ephemeral"})


@dataclass(frozen=True)
class InboundMessage:
    wa_message_id: str
    phone: str
    name: str | None
    kind: str
    text: str | None
    timestamp: datetime
    media_id: str | None = None
    media_mime: str | None = None
    reply_id: str | None = None

    @property
    def has_media(self) -> bool:
        return self.media_id is not None


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
    if kind == "location":
        return _location_text(message.get("location") or {})
    if kind in MEDIA_KINDS:
        caption = (message.get(kind) or {}).get("caption")
        return str(caption) if caption else None
    return None


def _location_text(location: dict[str, Any]) -> str | None:
    try:
        lat, lon = float(location["latitude"]), float(location["longitude"])
    except (KeyError, TypeError, ValueError):
        return None
    label = ", ".join(str(location[k]) for k in ("name", "address") if location.get(k))
    place = f"{label} " if label else ""
    return f"[localização enviada] {place}(latitude {lat:.5f}, longitude {lon:.5f})"


def _reply_id_of(message: dict[str, Any], kind: str) -> str | None:
    if kind == "button":
        payload = (message.get("button") or {}).get("payload")
        return str(payload) if payload else None
    if kind == "interactive":
        interactive = message.get("interactive") or {}
        reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
        reply_id = reply.get("id")
        return str(reply_id) if reply_id else None
    return None


def _media_of(message: dict[str, Any], kind: str) -> tuple[str | None, str | None]:
    if kind not in MEDIA_KINDS:
        return None, None
    media = message.get(kind) or {}
    media_id = media.get("id")
    mime = media.get("mime_type")
    return (str(media_id) if media_id else None), (str(mime) if mime else None)


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
                    media_id, media_mime = _media_of(raw, kind)
                    messages.append(
                        InboundMessage(
                            wa_message_id=str(raw["id"]),
                            phone=phone,
                            name=names.get(phone),
                            kind=kind,
                            text=_text_of(raw, kind),
                            timestamp=datetime.fromtimestamp(int(raw["timestamp"]), UTC),
                            media_id=media_id,
                            media_mime=media_mime,
                            reply_id=_reply_id_of(raw, kind),
                        )
                    )
                except (KeyError, TypeError, ValueError, AttributeError):
                    continue
    return messages

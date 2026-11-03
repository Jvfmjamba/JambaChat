from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

GRAPH_URL = "https://graph.facebook.com"
MAX_TEXT_LENGTH = 4096
MEDIA_HOSTS = ("fbsbx.com", "facebook.com", "whatsapp.net", "fbcdn.net")


class WhatsAppError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class MediaTooLargeError(WhatsAppError):
    pass


def split_text(text: str, limit: int = MAX_TEXT_LENGTH) -> list[str]:
    chunks: list[str] = []
    text = text.strip()
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        chunks.append(text)
    return chunks


@dataclass(frozen=True)
class Media:
    content: bytes
    mime_type: str


class WhatsAppClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        access_token: str,
        phone_number_id: str,
        api_version: str,
        timeout_seconds: float = 15.0,
    ) -> None:
        self._http = http
        self._graph = f"{GRAPH_URL}/{api_version}"
        self._url = f"{self._graph}/{phone_number_id}/messages"
        self._headers = {"Authorization": f"Bearer {access_token}"}
        self._timeout = timeout_seconds

    async def send_text(self, to: str, text: str) -> list[str]:
        ids: list[str] = []
        for chunk in split_text(text):
            data = await self._post(
                {
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    "to": to,
                    "type": "text",
                    "text": {"body": chunk, "preview_url": False},
                }
            )
            ids.append(_message_id(data))
        return ids

    async def send_template(
        self, to: str, name: str, params: Sequence[str] = (), language: str = "pt_BR"
    ) -> str:
        template: dict[str, Any] = {"name": name, "language": {"code": language}}
        if params:
            template["components"] = [
                {"type": "body", "parameters": [{"type": "text", "text": p} for p in params]}
            ]
        data = await self._post(
            {"messaging_product": "whatsapp", "to": to, "type": "template", "template": template}
        )
        return _message_id(data)

    async def mark_read(self, message_id: str, *, typing: bool = False) -> None:
        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id,
        }
        if typing:
            payload["typing_indicator"] = {"type": "text"}
        await self._post(payload)

    async def download_media(self, media_id: str, max_bytes: int) -> Media:
        if not media_id.isdigit():
            raise WhatsAppError("id de mídia inválido")
        info = await self._request("GET", f"{self._graph}/{media_id}")
        url, mime = info.get("url"), info.get("mime_type")
        if not isinstance(url, str) or not _is_meta_url(url):
            raise WhatsAppError("whatsapp não devolveu um endereço de mídia confiável")
        size = info.get("file_size")
        if isinstance(size, int) and size > max_bytes:
            raise MediaTooLargeError(f"mídia de {size} bytes passa do limite")
        try:
            async with self._http.stream(
                "GET", url, headers=self._headers, timeout=self._timeout * 2
            ) as response:
                if response.status_code >= 400:
                    raise WhatsAppError(f"download da mídia respondeu {response.status_code}")
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise MediaTooLargeError("mídia passa do limite")
                    chunks.append(chunk)
        except httpx.HTTPError as exc:
            raise WhatsAppError(f"falha de rede ao baixar mídia: {type(exc).__name__}") from exc
        return Media(b"".join(chunks), str(mime or "application/octet-stream"))

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", self._url, payload)

    async def _request(
        self, method: str, url: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        try:
            response = await self._http.request(
                method, url, json=payload, headers=self._headers, timeout=self._timeout
            )
        except httpx.HTTPError as exc:
            raise WhatsAppError(f"falha de rede: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            raise WhatsAppError(
                f"whatsapp respondeu {response.status_code}: {response.text[:300]}",
                status_code=response.status_code,
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise WhatsAppError("resposta do whatsapp não é JSON") from exc
        if not isinstance(data, dict):
            raise WhatsAppError("formato de resposta inesperado do whatsapp")
        return data


def _is_meta_url(url: str) -> bool:
    # o token de acesso vai no download, então só aceitamos domínios da Meta
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and (
        host in MEDIA_HOSTS or host.endswith(tuple(f".{h}" for h in MEDIA_HOSTS))
    )


def _message_id(data: dict[str, Any]) -> str:
    try:
        return str(data["messages"][0]["id"])
    except (KeyError, IndexError, TypeError) as exc:
        raise WhatsAppError("resposta do whatsapp sem id de mensagem") from exc

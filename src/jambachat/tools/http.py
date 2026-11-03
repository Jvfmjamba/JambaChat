import time
from typing import Any

import httpx
import structlog

from jambachat.tools.registry import ToolError

logger = structlog.get_logger(__name__)

DEFAULT_TIMEOUT = 10.0
USER_AGENT = "JambaChat/1.0 (+https://github.com/jvfmjamba/jambachat)"


class ServiceUnavailableError(ToolError):
    pass


async def request_json(
    http: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    service: str,
    params: dict[str, Any] | None = None,
    json: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT,
    not_found_ok: bool = False,
) -> Any:
    try:
        response = await http.request(
            method,
            url,
            params=params,
            json=json,
            headers={"User-Agent": USER_AGENT, **(headers or {})},
            timeout=timeout_seconds,
        )
    except httpx.HTTPError as exc:
        logger.warning("external_request_failed", service=service, error=type(exc).__name__)
        raise ServiceUnavailableError(
            f"o serviço de {service} não respondeu; tente mais tarde"
        ) from exc
    if response.status_code == 404 and not_found_ok:
        return None
    if response.status_code >= 400:
        logger.warning("external_request_error", service=service, status=response.status_code)
        raise ServiceUnavailableError(f"o serviço de {service} está indisponível agora")
    try:
        return response.json()
    except ValueError as exc:
        raise ServiceUnavailableError(f"o serviço de {service} respondeu algo inesperado") from exc


class TTLCache:
    def __init__(self, ttl_seconds: float, max_items: int = 256) -> None:
        self._ttl = ttl_seconds
        self._max = max_items
        self._items: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        item = self._items.get(key)
        if item is None:
            return None
        expires, value = item
        if expires < time.monotonic():
            self._items.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        if len(self._items) >= self._max:
            self._items.pop(next(iter(self._items)))
        self._items[key] = (time.monotonic() + self._ttl, value)

import asyncio
from typing import Any

import httpx
import structlog

from jambachat.llm.types import LLMError

logger = structlog.get_logger(__name__)

RETRY_DELAYS: tuple[float, ...] = (1.0, 3.0)
MAX_RETRY_AFTER = 10.0
RETRYABLE_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504, 529})


def _retry_after(response: httpx.Response, default: float) -> float:
    try:
        value = float(response.headers.get("retry-after", default))
    except ValueError:
        return default
    return min(max(value, 0.0), MAX_RETRY_AFTER)


async def post_json(
    http: httpx.AsyncClient,
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout_seconds: float,
) -> dict[str, Any]:
    attempts = len(RETRY_DELAYS) + 1
    for attempt in range(attempts):
        last = attempt == attempts - 1
        delay = 0.0 if last else RETRY_DELAYS[attempt]
        try:
            response = await http.post(url, json=payload, headers=headers, timeout=timeout_seconds)
        except (httpx.ConnectError, httpx.RemoteProtocolError) as exc:
            if last:
                raise LLMError(f"falha de rede ao chamar o provedor: {type(exc).__name__}") from exc
            logger.warning("llm_retry", reason=type(exc).__name__, attempt=attempt + 1)
            await asyncio.sleep(delay)
            continue
        except httpx.HTTPError as exc:
            raise LLMError(f"falha de rede ao chamar o provedor: {type(exc).__name__}") from exc
        if response.status_code in RETRYABLE_STATUS and not last:
            logger.warning("llm_retry", status=response.status_code, attempt=attempt + 1)
            await asyncio.sleep(_retry_after(response, delay))
            continue
        return _decode(response)
    raise AssertionError("unreachable")  # pragma: no cover


def _decode(response: httpx.Response) -> dict[str, Any]:
    if response.status_code >= 400:
        raise LLMError(
            f"provedor respondeu {response.status_code}: {response.text[:300]}",
            status_code=response.status_code,
        )
    try:
        data = response.json()
    except ValueError as exc:
        raise LLMError("provedor devolveu uma resposta que não é JSON") from exc
    if not isinstance(data, dict):
        raise LLMError("formato de resposta inesperado do provedor")
    return data

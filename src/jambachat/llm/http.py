from typing import Any

import httpx

from jambachat.llm.types import LLMError


async def post_json(
    http: httpx.AsyncClient,
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout_seconds: float,
) -> dict[str, Any]:
    try:
        response = await http.post(url, json=payload, headers=headers, timeout=timeout_seconds)
    except httpx.HTTPError as exc:
        raise LLMError(f"falha de rede ao chamar o provedor: {type(exc).__name__}") from exc
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

import ipaddress
from urllib.parse import urlsplit

import httpx

from jambachat.config import Settings
from jambachat.llm.anthropic import DEFAULT_BASE_URL, AnthropicClient
from jambachat.llm.openai_compatible import OpenAICompatibleClient
from jambachat.llm.types import LLMClient, LLMError

LOCAL_SUFFIXES = (".local", ".internal", ".lan", ".home.arpa")


class InsecureEndpointError(LLMError):
    pass


def _is_local_host(host: str) -> bool:
    if host == "localhost" or "." not in host or host.endswith(LOCAL_SUFFIXES):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_private or address.is_link_local


def ensure_secure_endpoint(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme == "https":
        return
    if parts.scheme == "http" and parts.hostname and _is_local_host(parts.hostname):
        return
    raise InsecureEndpointError(
        f"endpoint de IA recusado: {parts.scheme}://{parts.hostname}. "
        "Use https, ou http apenas para servidores na sua máquina ou rede local."
    )


def create_llm_client(settings: Settings, http: httpx.AsyncClient) -> LLMClient:
    api_key = settings.llm_api_key.get_secret_value()

    if settings.llm_provider == "anthropic":
        base_url = settings.llm_base_url or DEFAULT_BASE_URL
        ensure_secure_endpoint(base_url)
        if not api_key:
            raise LLMError("LLM_API_KEY é obrigatória para o provedor anthropic")
        return AnthropicClient(
            http,
            base_url=base_url,
            api_key=api_key,
            model=settings.llm_model,
            max_tokens=settings.llm_max_tokens,
            timeout=settings.llm_timeout_seconds,
        )

    if not settings.llm_base_url:
        raise LLMError("LLM_BASE_URL é obrigatória para o provedor openai_compatible")
    ensure_secure_endpoint(settings.llm_base_url)
    return OpenAICompatibleClient(
        http,
        base_url=settings.llm_base_url,
        api_key=api_key,
        model=settings.llm_model,
        max_tokens=settings.llm_max_tokens,
        timeout=settings.llm_timeout_seconds,
    )

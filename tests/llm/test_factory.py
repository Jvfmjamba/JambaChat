import httpx
import pytest

from jambachat.config import Settings
from jambachat.llm.anthropic import AnthropicClient
from jambachat.llm.factory import InsecureEndpointError, create_llm_client, ensure_secure_endpoint
from jambachat.llm.openai_compatible import OpenAICompatibleClient
from jambachat.llm.types import LLMError


@pytest.mark.parametrize(
    "url",
    [
        "https://generativelanguage.googleapis.com/v1beta/openai",
        "http://localhost:11434/v1",
        "http://127.0.0.1:1234/v1",
        "http://192.168.0.20:11434/v1",
        "http://ollama:11434/v1",
        "http://host.docker.internal:11434/v1",
    ],
)
def test_secure_or_local_endpoints_are_accepted(url: str) -> None:
    ensure_secure_endpoint(url)


@pytest.mark.parametrize(
    "url", ["http://api.groq.com/openai/v1", "http://8.8.8.8/v1", "ftp://localhost/v1", "x"]
)
def test_public_plaintext_endpoints_are_rejected(url: str) -> None:
    with pytest.raises(InsecureEndpointError):
        ensure_secure_endpoint(url)


def settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


async def test_factory_builds_each_provider() -> None:
    async with httpx.AsyncClient() as http:
        local = create_llm_client(settings(llm_base_url="http://localhost:11434/v1"), http)
        claude = create_llm_client(settings(llm_provider="anthropic", llm_api_key="k"), http)

    assert isinstance(local, OpenAICompatibleClient)
    assert isinstance(claude, AnthropicClient)


@pytest.mark.parametrize(
    "overrides",
    [
        {"llm_base_url": ""},
        {"llm_base_url": "http://api.exemplo.com/v1"},
        {"llm_provider": "anthropic"},
    ],
)
async def test_factory_rejects_invalid_configuration(overrides: dict[str, object]) -> None:
    async with httpx.AsyncClient() as http:
        with pytest.raises(LLMError):
            create_llm_client(settings(**overrides), http)

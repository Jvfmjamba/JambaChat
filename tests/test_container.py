import pytest

from jambachat.container import build_container
from jambachat.llm.types import LLMError
from tests.fakes import make_settings


async def test_build_container_wires_optional_features() -> None:
    container = build_container(
        make_settings(
            tavily_api_key="tvly",
            stt_base_url="https://api.groq.com/openai/v1",
            scheduler_enabled=True,
        )
    )
    try:
        assert container.scheduler is not None
        assert container.conversation._transcriber is not None
        assert container.conversation._orchestrator.tool_count == 29
    finally:
        await container.aclose()


async def test_build_container_without_optional_features() -> None:
    container = build_container(make_settings(scheduler_enabled=False))
    try:
        assert container.scheduler is None
        assert container.conversation._transcriber is None
        assert container.conversation._orchestrator.tool_count == 28
    finally:
        await container.aclose()


async def test_build_container_rejects_insecure_endpoints() -> None:
    with pytest.raises(LLMError):
        build_container(make_settings(stt_base_url="http://example.com/v1"))

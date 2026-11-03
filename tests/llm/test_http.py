import httpx
import pytest
import respx

from jambachat.llm.http import post_json
from jambachat.llm.types import LLMError

URL = "https://llm.example.com/v1/chat"


async def call() -> dict[str, object]:
    async with httpx.AsyncClient() as http:
        return await post_json(http, URL, {}, {}, 5)


@respx.mock
async def test_retries_transient_errors_then_succeeds() -> None:
    route = respx.post(URL).mock(
        side_effect=[
            httpx.Response(429, headers={"retry-after": "30"}),
            httpx.ConnectError("off"),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    assert await call() == {"ok": True}
    assert route.call_count == 3


@respx.mock
async def test_gives_up_after_retries() -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(503, text="ocupado"))
    with pytest.raises(LLMError) as info:
        await call()
    assert info.value.status_code == 503
    assert route.call_count == 3


@respx.mock
@pytest.mark.parametrize("outcome", [httpx.Response(400, text="ruim"), httpx.ReadTimeout("lento")])
async def test_does_not_retry_permanent_errors_or_timeouts(
    outcome: httpx.Response | Exception,
) -> None:
    route = respx.post(URL).mock(side_effect=[outcome])
    with pytest.raises(LLMError):
        await call()
    assert route.call_count == 1

from datetime import date
import logging

import httpx
import pytest

from jambachat.finance.errors import FinanceAuthError, FinanceError
from jambachat.finance.pluggy import PluggyProvider
from jambachat.logging import configure_logging


def _provider(handler: httpx.MockTransport) -> PluggyProvider:
    return PluggyProvider(
        httpx.AsyncClient(transport=handler),
        client_id="client",
        client_secret="secret",
        base_url="https://pluggy.test",
    )


def test_httpx_logger_uses_warning_level() -> None:
    configure_logging("INFO", json=False)

    assert logging.getLogger("httpx").level == logging.WARNING


@pytest.mark.asyncio
async def test_reads_item_and_caches_api_key() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/auth":
            return httpx.Response(200, json={"apiKey": "key"})
        return httpx.Response(200, json={"status": "UPDATED", "connector": {"name": "Banco"}})

    provider = _provider(httpx.MockTransport(handler))

    assert (await provider.list_items("one")).institution == "Banco"
    assert (await provider.list_items("two")).status == "UPDATED"
    assert [request.url.path for request in calls] == ["/auth", "/items/one", "/items/two"]
    assert calls[1].headers["X-API-KEY"] == "key"


@pytest.mark.asyncio
async def test_refreshes_api_key_once_after_unauthorized_response() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/auth":
            return httpx.Response(200, json={"apiKey": f"key-{len(calls)}"})
        if request.headers["X-API-KEY"] == "key-1":
            return httpx.Response(401)
        return httpx.Response(200, json={"status": "UPDATED", "connector": {"name": "Banco"}})

    provider = _provider(httpx.MockTransport(handler))

    assert (await provider.list_items("item")).institution == "Banco"
    assert [request.url.path for request in calls] == [
        "/auth",
        "/items/item",
        "/auth",
        "/items/item",
    ]
    assert calls[-1].headers["X-API-KEY"] == "key-3"


@pytest.mark.asyncio
async def test_maps_transactions_without_exposing_extra_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth":
            return httpx.Response(200, json={"apiKey": "key"})
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "tx-1",
                        "date": "2026-10-01T12:00:00.000Z",
                        "amount": -12.34,
                        "description": "Café",
                        "category": {"name": "Food"},
                        "status": "POSTED",
                        "account": {"number": "123"},
                    },
                    {
                        "id": "tx-2",
                        "date": "2026-10-02",
                        "amount": "20.00",
                        "description": "Transferência",
                        "category": "TRANSFER",
                        "status": "PENDING",
                    },
                ]
            },
        )

    provider = _provider(httpx.MockTransport(handler))

    transactions = await provider.fetch_transactions("item", date(2026, 10, 1), date(2026, 10, 2))

    assert transactions[0].amount_cents == 1234
    assert transactions[0].kind == "expense"
    assert transactions[1].status == "PENDING"
    assert transactions[1].is_transfer is True


@pytest.mark.asyncio
async def test_retries_server_errors_and_raises_auth_error() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        if request.url.path == "/auth":
            return httpx.Response(200, json={"apiKey": "key"})
        attempts += 1
        return httpx.Response(503)

    provider = _provider(httpx.MockTransport(handler))

    with pytest.raises(FinanceError):
        await provider.delete_item("item")
    assert attempts == 2

    async def unauthorized(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth":
            return httpx.Response(200, json={"apiKey": "key"})
        return httpx.Response(401)

    provider = _provider(httpx.MockTransport(unauthorized))
    with pytest.raises(FinanceAuthError):
        await provider.delete_item("item")

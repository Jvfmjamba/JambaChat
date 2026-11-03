import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jambachat.llm.types import ToolCall
from jambachat.tools.catalog import build_registry
from jambachat.tools.registry import ToolContext, ToolRegistry
from jambachat.users.models import User
from jambachat.utilities.weather import FORECAST_URL, GEOCODING_URL
from tests.fakes import make_settings

NOW = datetime(2026, 11, 3, 15, 0, tzinfo=UTC)


@pytest.fixture
async def ctx(sessionmaker: async_sessionmaker[AsyncSession]) -> AsyncIterator[ToolContext]:
    async with sessionmaker() as session:
        user = User(phone="1", phone_hash="1", timezone="America/Sao_Paulo")
        session.add(user)
        await session.flush()
        yield ToolContext(
            session=session,
            user=user,
            settings=make_settings(),
            now=NOW.astimezone(ZoneInfo(user.timezone)),
        )


@pytest.fixture
async def registry() -> AsyncIterator[ToolRegistry]:
    async with httpx.AsyncClient() as http:
        yield build_registry(make_settings(tavily_api_key="tvly-teste"), http)


async def run(registry: ToolRegistry, ctx: ToolContext, name: str, **args: Any) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(await registry.execute(ToolCall("c", name, args), ctx))
    return result


async def test_registry_without_http_has_only_offline_tools() -> None:
    names = {spec.name for spec in build_registry().specs()}
    assert {"calculate", "date_calc", "add_entry", "create_reminder"} <= names
    assert not names & {"web_search", "weather_forecast", "currency_exchange", "lookup_cep"}


async def test_search_requires_api_key() -> None:
    async with httpx.AsyncClient() as http:
        names = {spec.name for spec in build_registry(make_settings(), http).specs()}
    assert "weather_forecast" in names
    assert "web_search" not in names


async def test_calculate_tool(registry: ToolRegistry, ctx: ToolContext) -> None:
    result = await run(registry, ctx, "calculate", expression="1234.5 * 2")
    assert result["result"] == 2469
    assert result["formatted"] == "2.469"
    assert "inválida" in (await run(registry, ctx, "calculate", expression="x + 1"))["error"]


async def test_date_calc_tool(registry: ToolRegistry, ctx: ToolContext) -> None:
    diff = await run(registry, ctx, "date_calc", operation="diferenca", other_date="2026-12-25")
    assert diff["days"] == 52
    assert diff["from"] == "terça-feira, 03/11/2026"

    added = await run(registry, ctx, "date_calc", operation="somar", date="2026-11-03", days=30)
    assert added["result"] == "quinta-feira, 03/12/2026"

    weekday = await run(registry, ctx, "date_calc", operation="dia_da_semana", date="2027-01-01")
    assert weekday["date"] == "sexta-feira, 01/01/2027"

    missing = await run(registry, ctx, "date_calc", operation="diferenca")
    assert "other_date" in missing["error"]


@respx.mock
async def test_currency_exchange(registry: ToolRegistry, ctx: ToolContext) -> None:
    route = respx.get("https://economia.awesomeapi.com.br/json/last/USD-BRL").respond(
        json={
            "USDBRL": {
                "name": "Dólar Americano/Real Brasileiro",
                "bid": "5.4321",
                "ask": "5.4400",
                "high": "5.50",
                "low": "5.40",
                "pctChange": "0.3",
                "create_date": "2026-11-03 11:59:00",
            }
        }
    )
    result = await run(registry, ctx, "currency_exchange", from_currency="dólar", amount=100)
    again = await run(registry, ctx, "currency_exchange", from_currency="USD")

    assert result["rate"] == 5.4321
    assert result["converted_formatted"] == "R$ 543,21"
    assert result["updated_at"] == "03/11/2026 11:59"
    assert again["pair"] == "USD/BRL"
    assert route.call_count == 1


@respx.mock
async def test_currency_exchange_errors(registry: ToolRegistry, ctx: ToolContext) -> None:
    respx.get("https://economia.awesomeapi.com.br/json/last/XYZ-BRL").respond(404, json={})
    respx.get("https://economia.awesomeapi.com.br/json/last/EUR-BRL").mock(
        side_effect=httpx.ConnectTimeout("timeout")
    )

    assert (
        "não encontrei"
        in (await run(registry, ctx, "currency_exchange", from_currency="XYZ"))["error"]
    )
    assert (
        "não respondeu"
        in (await run(registry, ctx, "currency_exchange", from_currency="EUR"))["error"]
    )
    assert (
        "moeda inválida"
        in (await run(registry, ctx, "currency_exchange", from_currency="12"))["error"]
    )
    assert "iguais" in (await run(registry, ctx, "currency_exchange", from_currency="BRL"))["error"]


@respx.mock
async def test_weather_prefers_state_hint(registry: ToolRegistry, ctx: ToolContext) -> None:
    geocoding = respx.get(GEOCODING_URL).respond(
        json={
            "results": [
                {
                    "name": "Valença",
                    "admin1": "Bahia",
                    "country": "Brasil",
                    "country_code": "BR",
                    "latitude": -13.3,
                    "longitude": -39.0,
                },
                {
                    "name": "Valença",
                    "admin1": "Rio de Janeiro",
                    "country": "Brasil",
                    "country_code": "BR",
                    "latitude": -22.2,
                    "longitude": -43.7,
                },
            ]
        }
    )
    forecast = respx.get(FORECAST_URL).respond(
        json={
            "current": {
                "temperature_2m": 24.1,
                "apparent_temperature": 25.0,
                "relative_humidity_2m": 70,
                "weather_code": 2,
                "wind_speed_10m": 8.0,
                "precipitation": 0.0,
                "time": "2026-11-03T12:00",
            },
            "daily": {
                "time": ["2026-11-03"],
                "weather_code": [61],
                "temperature_2m_max": [28.0],
                "temperature_2m_min": [18.0],
                "precipitation_probability_max": [80],
                "precipitation_sum": [5.2],
                "uv_index_max": [9.1],
            },
        }
    )
    result = await run(registry, ctx, "weather_forecast", city="Valença, RJ")

    assert result["place"] == "Valença, Rio de Janeiro, Brasil"
    assert result["now"]["condition"] == "parcialmente nublado"
    assert result["days"][0]["condition"] == "chuva fraca"
    assert result["days"][0]["rain_chance_pct"] == 80
    assert geocoding.calls[0].request.url.params["name"] == "Valença"
    assert forecast.calls[0].request.url.params["latitude"] == "-22.2"


@respx.mock
async def test_weather_unknown_city(registry: ToolRegistry, ctx: ToolContext) -> None:
    respx.get(GEOCODING_URL).respond(json={"generationtime_ms": 0.1})
    result = await run(registry, ctx, "weather_forecast", city="Xyzzyville")
    assert "não encontrei a cidade" in result["error"]


@respx.mock
async def test_lookup_cep(registry: ToolRegistry, ctx: ToolContext) -> None:
    respx.get("https://viacep.com.br/ws/01001000/json/").respond(
        json={
            "cep": "01001-000",
            "logradouro": "Praça da Sé",
            "complemento": "",
            "bairro": "Sé",
            "localidade": "São Paulo",
            "uf": "SP",
            "ddd": "11",
        }
    )
    respx.get("https://viacep.com.br/ws/99999999/json/").respond(json={"erro": "true"})

    found = await run(registry, ctx, "lookup_cep", cep="01001-000")
    assert found["street"] == "Praça da Sé"
    assert found["complement"] is None
    assert "não encontrado" in (await run(registry, ctx, "lookup_cep", cep="99999999"))["error"]
    assert "8 dígitos" in (await run(registry, ctx, "lookup_cep", cep="123"))["error"]


async def test_weather_uses_home_city(registry: ToolRegistry, ctx: ToolContext) -> None:
    assert "qual cidade" in (await run(registry, ctx, "weather_forecast"))["error"]

    ctx.user.home_city = "Xyzzyville"
    with respx.mock:
        route = respx.get(GEOCODING_URL).respond(json={})
        await run(registry, ctx, "weather_forecast")
    assert route.calls[0].request.url.params["name"] == "Xyzzyville"

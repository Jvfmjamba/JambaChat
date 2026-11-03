from dataclasses import dataclass
from typing import Any

import httpx

from jambachat.tools.http import TTLCache, request_json
from jambachat.tools.registry import ToolError

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

WMO_CODES = {
    0: "céu limpo",
    1: "predomínio de sol",
    2: "parcialmente nublado",
    3: "nublado",
    45: "neblina",
    48: "neblina com geada",
    51: "garoa fraca",
    53: "garoa",
    55: "garoa forte",
    56: "garoa congelante",
    57: "garoa congelante forte",
    61: "chuva fraca",
    63: "chuva",
    65: "chuva forte",
    66: "chuva congelante",
    67: "chuva congelante forte",
    71: "neve fraca",
    73: "neve",
    75: "neve forte",
    77: "grãos de neve",
    80: "pancadas de chuva fracas",
    81: "pancadas de chuva",
    82: "pancadas de chuva fortes",
    85: "pancadas de neve",
    86: "pancadas de neve fortes",
    95: "trovoadas",
    96: "trovoadas com granizo",
    99: "trovoadas com granizo forte",
}

BR_STATES = {
    "AC": "Acre",
    "AL": "Alagoas",
    "AP": "Amapá",
    "AM": "Amazonas",
    "BA": "Bahia",
    "CE": "Ceará",
    "DF": "Distrito Federal",
    "ES": "Espírito Santo",
    "GO": "Goiás",
    "MA": "Maranhão",
    "MT": "Mato Grosso",
    "MS": "Mato Grosso do Sul",
    "MG": "Minas Gerais",
    "PA": "Pará",
    "PB": "Paraíba",
    "PR": "Paraná",
    "PE": "Pernambuco",
    "PI": "Piauí",
    "RJ": "Rio de Janeiro",
    "RN": "Rio Grande do Norte",
    "RS": "Rio Grande do Sul",
    "RO": "Rondônia",
    "RR": "Roraima",
    "SC": "Santa Catarina",
    "SP": "São Paulo",
    "SE": "Sergipe",
    "TO": "Tocantins",
}


@dataclass(frozen=True)
class Place:
    name: str
    region: str
    country: str
    latitude: float
    longitude: float

    @property
    def label(self) -> str:
        return ", ".join(p for p in (self.name, self.region, self.country) if p)


def describe_code(code: object) -> str:
    return (
        WMO_CODES.get(int(code), "condição desconhecida") if isinstance(code, int | float) else ""
    )


class WeatherClient:
    def __init__(self, http: httpx.AsyncClient, cache_seconds: float = 600) -> None:
        self._http = http
        self._places = TTLCache(24 * 3600)
        self._forecasts = TTLCache(cache_seconds)

    async def locate(self, query: str) -> Place:
        key = query.strip().casefold()
        cached = self._places.get(key)
        if isinstance(cached, Place):
            return cached
        name, _, hint = query.partition(",")
        hint = hint.strip()
        data = await request_json(
            self._http,
            "GET",
            GEOCODING_URL,
            service="previsão do tempo",
            params={"name": name.strip(), "count": 10, "language": "pt", "format": "json"},
        )
        results = data.get("results") if isinstance(data, dict) else None
        if not results:
            raise ToolError(f"não encontrei a cidade {query!r}")
        chosen = _pick(results, hint)
        place = Place(
            name=str(chosen.get("name", name)),
            region=str(chosen.get("admin1") or ""),
            country=str(chosen.get("country") or ""),
            latitude=float(chosen["latitude"]),
            longitude=float(chosen["longitude"]),
        )
        self._places.set(key, place)
        return place

    async def forecast(self, place: Place, days: int) -> dict[str, Any]:
        key = f"{place.latitude:.3f},{place.longitude:.3f},{days}"
        cached = self._forecasts.get(key)
        if isinstance(cached, dict):
            return cached
        data = await request_json(
            self._http,
            "GET",
            FORECAST_URL,
            service="previsão do tempo",
            params={
                "latitude": place.latitude,
                "longitude": place.longitude,
                "current": "temperature_2m,apparent_temperature,relative_humidity_2m,"
                "weather_code,wind_speed_10m,precipitation",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                "precipitation_probability_max,precipitation_sum,uv_index_max",
                "timezone": "auto",
                "forecast_days": days,
            },
        )
        if not isinstance(data, dict):
            raise ToolError("o serviço de previsão do tempo respondeu algo inesperado")
        result = _shape(data)
        self._forecasts.set(key, result)
        return result


def _pick(results: list[dict[str, Any]], hint: str) -> dict[str, Any]:
    if hint:
        wanted = {hint.casefold(), BR_STATES.get(hint.upper(), hint).casefold()}
        for item in results:
            fields = {
                str(item.get("admin1", "")).casefold(),
                str(item.get("country", "")).casefold(),
                str(item.get("country_code", "")).casefold(),
            }
            if wanted & fields:
                return item
    brazilian = [r for r in results if r.get("country_code") == "BR"]
    return (brazilian or results)[0]


def _shape(data: dict[str, Any]) -> dict[str, Any]:
    try:
        current = data.get("current") or {}
        daily = data.get("daily") or {}
        days = []
        for index, day in enumerate(daily.get("time") or []):
            days.append(
                {
                    "date": day,
                    "condition": describe_code(daily["weather_code"][index]),
                    "min_c": daily["temperature_2m_min"][index],
                    "max_c": daily["temperature_2m_max"][index],
                    "rain_chance_pct": (daily.get("precipitation_probability_max") or [None])[
                        index
                    ],
                    "rain_mm": (daily.get("precipitation_sum") or [None])[index],
                    "uv_index_max": (daily.get("uv_index_max") or [None])[index],
                }
            )
        return {
            "now": {
                "temperature_c": current.get("temperature_2m"),
                "feels_like_c": current.get("apparent_temperature"),
                "humidity_pct": current.get("relative_humidity_2m"),
                "wind_kmh": current.get("wind_speed_10m"),
                "precipitation_mm": current.get("precipitation"),
                "condition": describe_code(current.get("weather_code")),
                "local_time": current.get("time"),
            },
            "days": days,
        }
    except (KeyError, IndexError, TypeError) as exc:
        raise ToolError("o serviço de previsão do tempo respondeu algo inesperado") from exc

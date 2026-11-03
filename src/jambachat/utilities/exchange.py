import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from jambachat.tools.http import ServiceUnavailableError, TTLCache, request_json
from jambachat.tools.registry import ToolError

AWESOME_URL = "https://economia.awesomeapi.com.br/json/last/{pair}"
FRANKFURTER_URL = "https://api.frankfurter.dev/v1/latest"
CURRENCY = re.compile(r"^[A-Z]{3,4}$")
ALIASES = {
    "REAL": "BRL",
    "REAIS": "BRL",
    "DOLAR": "USD",
    "DÓLAR": "USD",
    "EURO": "EUR",
    "LIBRA": "GBP",
    "IENE": "JPY",
    "PESO": "ARS",
    "BITCOIN": "BTC",
    "ETHEREUM": "ETH",
}


@dataclass(frozen=True)
class Quote:
    base: str
    quote: str
    name: str
    bid: float
    ask: float
    high: float
    low: float
    pct_change: float
    updated_at: str


def normalize_currency(value: str) -> str:
    code = value.strip().upper()
    code = ALIASES.get(code, code)
    if not CURRENCY.fullmatch(code):
        raise ToolError(f"moeda inválida: {value}; use o código ISO, ex.: USD, EUR, BTC")
    return code


class ExchangeClient:
    def __init__(self, http: httpx.AsyncClient, cache_seconds: float = 120) -> None:
        self._http = http
        self._cache = TTLCache(cache_seconds)

    async def quote(self, base: str, quote: str) -> Quote:
        pair = f"{base}-{quote}"
        cached = self._cache.get(pair)
        if isinstance(cached, Quote):
            return cached
        try:
            result = await self._awesome(base, quote)
        except ServiceUnavailableError:
            result = await self._frankfurter(base, quote)
        self._cache.set(pair, result)
        return result

    async def _awesome(self, base: str, quote: str) -> Quote:
        data = await request_json(
            self._http,
            "GET",
            AWESOME_URL.format(pair=f"{base}-{quote}"),
            service="cotações",
            not_found_ok=True,
        )
        raw = data.get(f"{base}{quote}") if isinstance(data, dict) else None
        if not isinstance(raw, dict):
            raise ToolError(f"não encontrei cotação para {base}/{quote}")
        return _parse(base, quote, raw)

    async def _frankfurter(self, base: str, quote: str) -> Quote:
        """Reserva com taxas de referência do BCE (só moedas tradicionais, sem cripto)."""
        data = await request_json(
            self._http,
            "GET",
            FRANKFURTER_URL,
            service="cotações",
            params={"base": base, "symbols": quote},
            not_found_ok=True,
        )
        rate = ((data or {}).get("rates") or {}).get(quote) if isinstance(data, dict) else None
        if not isinstance(rate, int | float):
            raise ServiceUnavailableError("o serviço de cotações está indisponível agora")
        day = str(data.get("date", ""))
        return Quote(
            base=base,
            quote=quote,
            name=f"{base}/{quote} (referência do BCE)",
            bid=float(rate),
            ask=float(rate),
            high=float(rate),
            low=float(rate),
            pct_change=0.0,
            updated_at=day,
        )


def _parse(base: str, quote: str, raw: dict[str, Any]) -> Quote:
    try:
        created = str(raw.get("create_date", ""))
        try:
            updated = datetime.fromisoformat(created).strftime("%d/%m/%Y %H:%M")
        except ValueError:
            updated = created
        return Quote(
            base=base,
            quote=quote,
            name=str(raw.get("name", f"{base}/{quote}")),
            bid=float(raw["bid"]),
            ask=float(raw.get("ask", raw["bid"])),
            high=float(raw.get("high", raw["bid"])),
            low=float(raw.get("low", raw["bid"])),
            pct_change=float(raw.get("pctChange", 0)),
            updated_at=updated,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ToolError("o serviço de cotações respondeu algo inesperado") from exc

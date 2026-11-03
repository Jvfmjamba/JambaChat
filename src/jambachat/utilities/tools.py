from datetime import date
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

from jambachat.money import format_brl
from jambachat.tools.registry import Tool, ToolContext, ToolError
from jambachat.utilities.calculator import CalculationError, evaluate
from jambachat.utilities.cep import CepClient
from jambachat.utilities.dates import describe_date, describe_gap, shift
from jambachat.utilities.exchange import ExchangeClient, normalize_currency
from jambachat.utilities.weather import WeatherClient


class CalculateArgs(BaseModel):
    expression: str = Field(
        min_length=1,
        max_length=300,
        description="Expressão matemática com ponto decimal, ex.: (1200 * 0.15) + sqrt(16)",
    )


class DateCalcArgs(BaseModel):
    operation: Literal["diferenca", "somar", "dia_da_semana"] = Field(
        description="diferenca entre duas datas; somar um prazo a uma data; "
        "ou dia_da_semana de uma data"
    )
    date: str | None = Field(default=None, description="Data base AAAA-MM-DD; padrão: hoje")
    other_date: str | None = Field(
        default=None, description="Segunda data AAAA-MM-DD, para diferenca"
    )
    days: int = Field(
        default=0, ge=-36500, le=36500, description="Dias a somar (pode ser negativo)"
    )
    weeks: int = Field(default=0, ge=-5200, le=5200)
    months: int = Field(default=0, ge=-1200, le=1200)
    years: int = Field(default=0, ge=-100, le=100)


class ExchangeArgs(BaseModel):
    from_currency: str = Field(description="Código da moeda de origem, ex.: USD, EUR, BTC")
    to_currency: str = Field(default="BRL", description="Código da moeda de destino")
    amount: float | None = Field(
        default=None, gt=0, le=1e12, description="Quantia a converter, se houver"
    )


class WeatherArgs(BaseModel):
    city: str = Field(
        min_length=2, max_length=100, description='Cidade, opcionalmente com UF: "Campinas, SP"'
    )
    days: int = Field(default=1, ge=1, le=7, description="Quantos dias de previsão, de 1 a 7")


class CepArgs(BaseModel):
    cep: str = Field(description="CEP com ou sem traço")


def _parse_date(value: str | None, ctx: ToolContext) -> date:
    if not value:
        return ctx.now.date()
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError as exc:
        raise ToolError("data inválida; use AAAA-MM-DD") from exc


async def calculate(ctx: ToolContext, args: CalculateArgs) -> dict[str, Any]:
    try:
        result = evaluate(args.expression)
    except CalculationError as exc:
        raise ToolError(str(exc)) from exc
    pretty = f"{result:,.10g}" if isinstance(result, float) else f"{result:,}"
    pretty = pretty.replace(",", "_").replace(".", ",").replace("_", ".")
    return {"expression": args.expression, "result": result, "formatted": pretty}


async def date_calc(ctx: ToolContext, args: DateCalcArgs) -> dict[str, Any]:
    base = _parse_date(args.date, ctx)
    if args.operation == "dia_da_semana":
        return {"date": describe_date(base)}
    if args.operation == "somar":
        try:
            target = shift(
                base, days=args.days, weeks=args.weeks, months=args.months, years=args.years
            )
        except (OverflowError, ValueError) as exc:
            raise ToolError("data fora do intervalo suportado") from exc
        return {"from": describe_date(base), "result": describe_date(target)}
    if not args.other_date:
        raise ToolError("informe other_date para calcular a diferença")
    other = _parse_date(args.other_date, ctx)
    return {"from": describe_date(base), "to": describe_date(other), **describe_gap(base, other)}


def build_utility_tools(http: httpx.AsyncClient) -> list[Tool]:
    exchange = ExchangeClient(http)
    weather = WeatherClient(http)
    cep = CepClient(http)

    async def convert(ctx: ToolContext, args: ExchangeArgs) -> dict[str, Any]:
        base = normalize_currency(args.from_currency)
        target = normalize_currency(args.to_currency)
        if base == target:
            raise ToolError("as moedas de origem e destino são iguais")
        quote = await exchange.quote(base, target)
        result: dict[str, Any] = {
            "pair": f"{base}/{target}",
            "name": quote.name,
            "rate": quote.bid,
            "day_high": quote.high,
            "day_low": quote.low,
            "change_pct": quote.pct_change,
            "updated_at": quote.updated_at,
        }
        if args.amount is not None:
            converted = args.amount * quote.bid
            result["amount"] = args.amount
            result["converted"] = round(converted, 2)
            if target == "BRL":
                result["converted_formatted"] = format_brl(round(converted * 100))
        return result

    async def forecast(ctx: ToolContext, args: WeatherArgs) -> dict[str, Any]:
        place = await weather.locate(args.city)
        return {"place": place.label, **await weather.forecast(place, args.days)}

    async def lookup_cep(ctx: ToolContext, args: CepArgs) -> dict[str, Any]:
        return await cep.lookup(args.cep)

    return [
        Tool(
            "currency_exchange",
            "Cotação atual entre moedas (inclui cripto) e conversão de valores.",
            ExchangeArgs,
            convert,
        ),
        Tool(
            "weather_forecast",
            "Clima agora e previsão de até 7 dias para uma cidade.",
            WeatherArgs,
            forecast,
        ),
        Tool("lookup_cep", "Consulta o endereço de um CEP brasileiro.", CepArgs, lookup_cep),
    ]


OFFLINE_TOOLS = [
    Tool(
        "calculate",
        "Calcula expressões matemáticas com precisão. Use sempre que houver conta, "
        "porcentagem, juros ou divisão de valores.",
        CalculateArgs,
        calculate,
    ),
    Tool(
        "date_calc",
        "Contas com datas: diferença em dias, dias úteis, meses e anos; somar prazos; "
        "descobrir o dia da semana.",
        DateCalcArgs,
        date_calc,
    ),
]

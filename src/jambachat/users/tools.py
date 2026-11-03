import re
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo, available_timezones

from pydantic import Field, field_validator

from jambachat.timeutils import format_local
from jambachat.tools.registry import Tool, ToolArgs, ToolContext, ToolError

TIMEZONE_ALIASES = {
    "brasilia": "America/Sao_Paulo",
    "brasília": "America/Sao_Paulo",
    "sao paulo": "America/Sao_Paulo",
    "são paulo": "America/Sao_Paulo",
    "manaus": "America/Manaus",
    "amazonas": "America/Manaus",
    "acre": "America/Rio_Branco",
    "rio branco": "America/Rio_Branco",
    "fernando de noronha": "America/Noronha",
    "noronha": "America/Noronha",
    "cuiaba": "America/Cuiaba",
    "cuiabá": "America/Cuiaba",
    "fortaleza": "America/Fortaleza",
    "recife": "America/Recife",
    "belem": "America/Belem",
    "belém": "America/Belem",
    "lisboa": "Europe/Lisbon",
    "portugal": "Europe/Lisbon",
}


@lru_cache
def _zones() -> dict[str, str]:
    return {zone.casefold(): zone for zone in available_timezones()}


def resolve_timezone(value: str) -> str:
    text = value.strip()
    alias = TIMEZONE_ALIASES.get(text.casefold())
    if alias:
        return alias
    zone = _zones().get(text.casefold().replace(" ", "_"))
    if zone is None:
        raise ToolError(f"fuso horário desconhecido: {value}; use o nome IANA, ex.: America/Manaus")
    return zone


# Nome e cidade entram no prompt de sistema de toda conversa: só texto simples,
# para que ninguém grave instruções ali (ex.: via página da web lida pela IA).
NAME_RE = re.compile(r"^[^\W_][\w .'\-]*$")
CITY_RE = re.compile(r"^[\w .,'\-()/]*$")


class UpdateProfileArgs(ToolArgs):
    name: str | None = Field(
        default=None, min_length=1, max_length=60, description="Como o usuário quer ser chamado"
    )
    timezone: str | None = Field(
        default=None, max_length=64, description="Fuso IANA, ex.: America/Manaus"
    )
    home_city: str | None = Field(
        default=None,
        max_length=100,
        description='Cidade padrão para clima, ex.: "Recife, PE". Texto vazio remove.',
    )

    @field_validator("name")
    @classmethod
    def _plain_name(cls, value: str | None) -> str | None:
        if value is not None and not NAME_RE.fullmatch(value.strip()):
            raise ValueError("use só letras, números, espaço, ponto, hífen ou apóstrofo")
        return value

    @field_validator("home_city")
    @classmethod
    def _plain_city(cls, value: str | None) -> str | None:
        if value is not None and not CITY_RE.fullmatch(value.strip()):
            raise ValueError("cidade com caracteres inválidos")
        return value


class GetProfileArgs(ToolArgs):
    pass


def _view(ctx: ToolContext) -> dict[str, Any]:
    tz = ZoneInfo(ctx.user.timezone)
    return {
        "name": ctx.user.name,
        "timezone": ctx.user.timezone,
        "local_time": format_local(ctx.now, tz),
        "home_city": ctx.user.home_city,
    }


async def update_profile(ctx: ToolContext, args: UpdateProfileArgs) -> dict[str, Any]:
    if args.name is None and args.timezone is None and args.home_city is None:
        raise ToolError("nada para alterar")
    if args.timezone is not None:
        ctx.user.timezone = resolve_timezone(args.timezone)
    if args.name is not None:
        ctx.user.name = args.name.strip()
    if args.home_city is not None:
        ctx.user.home_city = args.home_city.strip() or None
    await ctx.session.flush()
    return {"updated": _view(ctx)}


async def get_profile(ctx: ToolContext, args: GetProfileArgs) -> dict[str, Any]:
    return _view(ctx)


PROFILE_TOOLS = [
    Tool(
        "update_profile",
        "Altera preferências do usuário: nome, fuso horário e cidade padrão.",
        UpdateProfileArgs,
        update_profile,
        destructive=True,
    ),
    Tool("get_profile", "Mostra as preferências salvas do usuário.", GetProfileArgs, get_profile),
]

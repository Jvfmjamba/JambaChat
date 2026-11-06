from datetime import UTC
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from pydantic import Field, model_validator

from jambachat.finance.errors import FinanceError
from jambachat.finance.importer import FinanceImporter
from jambachat.finance.models import FinanceConnection
from jambachat.finance.provider import FinanceProvider
from jambachat.finance.service import (
    FinanceConnectionError,
    FinanceService,
    FinanceSyncRateLimitError,
)
from jambachat.timeutils import format_local_date
from jambachat.tools.registry import Tool, ToolArgs, ToolContext, ToolError

logger = structlog.get_logger(__name__)


class ConnectBankArgs(ToolArgs):
    item_ref: str = Field(
        min_length=1, max_length=128, description="Identificador informado pelo usuario"
    )


class DisconnectBankArgs(ToolArgs):
    connection_id: str | None = Field(default=None, description="Id obtido em list_banks")
    institution: str | None = Field(default=None, min_length=1, max_length=128)
    confirm: bool = False

    @model_validator(mode="after")
    def validate_target(self) -> "DisconnectBankArgs":
        if bool(self.connection_id) == bool(self.institution):
            raise ValueError("informe connection_id ou institution")
        return self


class NoArgs(ToolArgs):
    pass


def _view(connection: FinanceConnection, timezone: str) -> dict[str, str | None]:
    labels = {"active": "ativa", "reconnect": "precisa reconectar"}
    status = labels.get(
        connection.status, "com erro" if connection.last_error else connection.status
    )
    return {
        "id": str(connection.id),
        "institution": connection.institution,
        "status": status,
        "last_import": (
            format_local_date(connection.last_synced_at, ZoneInfo(timezone))
            if connection.last_synced_at
            else None
        ),
    }


def build_finance_tools(
    provider: FinanceProvider, service: FinanceService, importer: FinanceImporter
) -> list[Tool]:
    async def connect_bank(ctx: ToolContext, args: ConnectBankArgs) -> dict[str, str]:
        try:
            connection = await service.connect(ctx.session, ctx.user, args.item_ref)
        except (FinanceError, FinanceConnectionError) as exc:
            raise ToolError(str(exc)) from exc
        return {"connected": connection.institution, "status": "ativa"}

    async def list_banks(ctx: ToolContext, args: NoArgs) -> dict[str, list[dict[str, str | None]]]:
        connections = await service.list_connections(ctx.session, ctx.user)
        return {"banks": [_view(connection, ctx.user.timezone) for connection in connections]}

    async def import_now(ctx: ToolContext, args: NoArgs) -> dict[str, int]:
        connections = await service.list_connections(ctx.session, ctx.user)
        if not connections:
            raise ToolError("nenhum banco conectado")
        try:
            for connection in connections:
                service.allow_manual_sync(connection, ctx.now.astimezone(UTC))
            imported = 0
            for connection in connections:
                imported += await importer.run(
                    ctx.session, ctx.user, connection, ctx.now.astimezone(UTC)
                )
        except FinanceSyncRateLimitError as exc:
            raise ToolError(str(exc)) from exc
        except FinanceError as exc:
            raise ToolError(str(exc)) from exc
        return {"imported": imported}

    async def disconnect_bank(ctx: ToolContext, args: DisconnectBankArgs) -> dict[str, Any]:
        connections = await service.list_connections(ctx.session, ctx.user)
        connection = next(
            (
                current
                for current in connections
                if str(current.id) == args.connection_id
                or current.institution.casefold() == (args.institution or "").strip().casefold()
            ),
            None,
        )
        if connection is None:
            raise ToolError("conexao nao encontrada")
        if not args.confirm:
            return {
                "confirmation_required": (
                    f"Confirme a desconexao de {connection.institution} antes de apagar."
                ),
                "connection": _view(connection, ctx.user.timezone),
            }
        aggregator_failed = False
        try:
            try:
                await provider.delete_item(connection.item_ref)
            except Exception as exc:
                logger.warning(
                    "finance_delete_item_failed",
                    connection_id=str(connection.id),
                    error=type(exc).__name__,
                )
                aggregator_failed = True
            await ctx.session.delete(connection)
        except FinanceConnectionError as exc:
            raise ToolError(str(exc)) from exc
        response: dict[str, Any] = {"disconnected": connection.institution}
        if aggregator_failed:
            response["warning"] = "A remoção no agregador falhou."
        return response

    return [
        Tool(
            "connect_bank",
            "Conecta um banco com o identificador enviado pelo usuario.",
            ConnectBankArgs,
            connect_bank,
        ),
        Tool("list_banks", "Lista os bancos conectados do usuario.", NoArgs, list_banks),
        Tool(
            "import_now", "Importa agora as transacoes dos bancos conectados.", NoArgs, import_now
        ),
        Tool(
            "disconnect_bank",
            "Desconecta um banco depois da confirmacao explicita do usuario.",
            DisconnectBankArgs,
            disconnect_bank,
            destructive=True,
        ),
    ]

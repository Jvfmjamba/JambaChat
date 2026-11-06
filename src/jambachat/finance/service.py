import re
import uuid
from contextlib import suppress
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from jambachat.finance.errors import FinanceAuthError, FinanceError
from jambachat.finance.models import FinanceConnection
from jambachat.finance.provider import FinanceProvider
from jambachat.users.models import User

MANUAL_SYNC_INTERVAL = timedelta(minutes=15)
_ITEM_REF = re.compile(r"[A-Za-z0-9_-]{1,128}")


class FinanceConnectionError(ValueError):
    pass


class FinanceSyncRateLimitError(ValueError):
    pass


class FinanceService:
    def __init__(self, provider: FinanceProvider) -> None:
        self._provider = provider

    async def connect(self, session: AsyncSession, user: User, item_ref: str) -> FinanceConnection:
        item_ref = item_ref.strip()
        if not _ITEM_REF.fullmatch(item_ref):
            raise FinanceConnectionError("identificador do banco inv\u00e1lido")
        item = await self._provider.list_items(item_ref)
        if item.status != "UPDATED":
            raise FinanceAuthError("a autoriza\u00e7\u00e3o do banco precisa ser renovada")
        connection = FinanceConnection(
            user_id=user.id,
            item_ref=item_ref,
            institution=item.institution,
            status="active",
        )
        try:
            async with session.begin_nested():
                session.add(connection)
                await session.flush()
        except IntegrityError as exc:
            raise FinanceConnectionError("conexao duplicada") from exc
        return connection

    async def list_connections(self, session: AsyncSession, user: User) -> list[FinanceConnection]:
        return list(
            await session.scalars(
                select(FinanceConnection)
                .where(FinanceConnection.user_id == user.id)
                .order_by(FinanceConnection.created_at)
            )
        )

    async def remove(self, session: AsyncSession, user: User, connection_id: str) -> None:
        try:
            connection = await session.get(FinanceConnection, uuid.UUID(connection_id))
        except ValueError as exc:
            raise FinanceConnectionError("conexao nao encontrada") from exc
        if connection is None or connection.user_id != user.id:
            raise FinanceConnectionError("conex\u00e3o n\u00e3o encontrada")
        with suppress(FinanceError):
            await self._provider.delete_item(connection.item_ref)
        await session.delete(connection)

    def allow_manual_sync(self, connection: FinanceConnection, now: datetime | None = None) -> None:
        now = now or datetime.now(UTC)
        if connection.last_manual_sync_at is not None:
            elapsed = now - connection.last_manual_sync_at.replace(tzinfo=UTC)
            if elapsed < MANUAL_SYNC_INTERVAL:
                raise FinanceSyncRateLimitError("aguarde antes de importar novamente")
        connection.last_manual_sync_at = now

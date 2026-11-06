import uuid
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jambachat.expenses.budgets import budget_alerts
from jambachat.expenses.models import Budget
from jambachat.finance.errors import FinanceAuthError
from jambachat.finance.importer import FinanceImporter
from jambachat.finance.models import FinanceConnection
from jambachat.timeutils import as_utc
from jambachat.users.models import User
from jambachat.whatsapp.client import WhatsAppClient

logger = structlog.get_logger(__name__)
SYNC_INTERVAL = timedelta(hours=24)
SERVICE_WINDOW = timedelta(hours=24)


class FinanceImportJob:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        importer: FinanceImporter,
        whatsapp: WhatsAppClient,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._importer = importer
        self._whatsapp = whatsapp

    async def run_once(self, now: datetime | None = None) -> int:
        now = now or datetime.now(UTC)
        imported = 0
        alerted_users: set[uuid.UUID] = set()
        async with self._sessionmaker() as session:
            connections = list(
                await session.scalars(
                    select(FinanceConnection)
                    .where(
                        FinanceConnection.status == "active",
                        or_(
                            FinanceConnection.last_synced_at.is_(None),
                            FinanceConnection.last_synced_at <= now - SYNC_INTERVAL,
                        ),
                    )
                    .order_by(FinanceConnection.created_at)
                )
            )
            for connection in connections:
                user = await session.get(User, connection.user_id)
                if user is None:
                    continue
                try:
                    async with session.begin_nested():
                        connection_imported = await self._importer.run(
                            session, user, connection, now
                        )
                        imported += connection_imported
                    if connection_imported and user.id not in alerted_users:
                        await self._send_budget_alerts(session, user, now)
                        alerted_users.add(user.id)
                except FinanceAuthError:
                    await self._reconnect(session, user, connection, now)
                except Exception as exc:
                    connection.last_error = type(exc).__name__
                    logger.warning(
                        "finance_import_failed",
                        connection_id=str(connection.id),
                        user_id=str(user.id),
                        error=type(exc).__name__,
                    )
            await session.commit()
        return imported

    async def _reconnect(
        self, session: AsyncSession, user: User, connection: FinanceConnection, now: datetime
    ) -> None:
        connection.status = "reconnect"
        connection.last_error = "FinanceAuthError"
        if connection.reconnect_notified_at is not None:
            return
        try:
            await self._whatsapp.send_text(
                user.phone,
                "Reconecte seu banco "
                f"{connection.institution} para retomar a importa\u00e7\u00e3o.",
            )
        except Exception as exc:
            logger.warning(
                "finance_reconnect_notification_failed",
                connection_id=str(connection.id),
                user_id=str(user.id),
                error=type(exc).__name__,
            )
            return
        connection.reconnect_notified_at = now

    async def _send_budget_alerts(self, session: AsyncSession, user: User, now: datetime) -> None:
        if user.last_inbound_at is None or now - as_utc(user.last_inbound_at) > SERVICE_WINDOW:
            return
        budgets = list(await session.scalars(select(Budget).where(Budget.user_id == user.id)))
        alerts: dict[str, dict[str, object]] = {}
        for budget in budgets:
            for alert in await budget_alerts(session, user, now, budget.category):
                if int(str(alert["used"]).removesuffix("%")) >= 80:
                    alerts[str(alert["category"])] = alert
        if not alerts:
            return
        lines = ["*Alerta de or\u00e7amento*"]
        lines.extend(
            f"{alert['category']}: {alert['used']} ({alert['spent_this_month']})"
            for alert in alerts.values()
        )
        try:
            await self._whatsapp.send_text(user.phone, "\n".join(lines))
        except Exception as exc:
            logger.warning(
                "finance_budget_notification_failed",
                user_id=str(user.id),
                error=type(exc).__name__,
            )

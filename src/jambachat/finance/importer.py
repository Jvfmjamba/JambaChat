from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from jambachat.expenses.categorize import guess_category
from jambachat.expenses.models import EntryKind, EntrySource
from jambachat.expenses.service import add_entry, find_match_for_import
from jambachat.finance.categories import map_category
from jambachat.finance.models import ExpenseLink, FinanceConnection, FinanceSuppression
from jambachat.finance.provider import FinanceProvider, RawTransaction
from jambachat.text import fold
from jambachat.users.models import User

INITIAL_SYNC_WINDOW = timedelta(days=30)
SYNC_MARGIN = timedelta(days=3)


class FinanceImporter:
    def __init__(self, provider: FinanceProvider) -> None:
        self._provider = provider

    async def run(
        self,
        session: AsyncSession,
        user: User,
        connection: FinanceConnection,
        now: datetime | None = None,
    ) -> int:
        now = now or datetime.now(UTC)
        since = self._since(connection, now.date())
        transactions = await self._provider.fetch_transactions(
            connection.item_ref, since, now.date()
        )
        imported = 0
        for transaction in transactions:
            if self._ignored(transaction):
                continue
            if await self._suppressed(session, user, transaction.id):
                continue
            try:
                async with session.begin_nested():
                    entry = await add_entry(
                        session,
                        user,
                        kind=EntryKind.EXPENSE
                        if transaction.kind == "expense"
                        else EntryKind.INCOME,
                        amount_cents=abs(transaction.amount_cents),
                        description=transaction.description,
                        category=self._category(transaction),
                        occurred_at=self._occurred_at(transaction.date, user.timezone),
                        source=EntrySource.OPEN_FINANCE,
                        external_id=transaction.id,
                    )
                    match = await find_match_for_import(
                        session, user, entry.kind, entry.amount_cents, entry.occurred_at
                    )
                    if match is not None:
                        session.add(
                            ExpenseLink(
                                user_id=user.id,
                                imported_expense_id=entry.id,
                                matched_expense_id=match.id,
                            )
                        )
                    imported += 1
            except IntegrityError as exc:
                if self._is_duplicate(exc):
                    continue
                raise
        connection.last_synced_at = now
        connection.last_error = None
        return imported

    @staticmethod
    def _since(connection: FinanceConnection, today: date) -> date:
        if connection.last_synced_at is None:
            return today - INITIAL_SYNC_WINDOW
        return connection.last_synced_at.date() - SYNC_MARGIN

    @staticmethod
    def _ignored(transaction: RawTransaction) -> bool:
        if transaction.status == "PENDING" or transaction.is_transfer:
            return True
        return "pagamento de fatura" in fold(
            transaction.description
        ) or "pagamento de fatura" in fold(transaction.category or "")

    @staticmethod
    def _is_duplicate(error: IntegrityError) -> bool:
        return "uq_expenses_external" in str(error.orig) or "UNIQUE constraint failed" in str(
            error.orig
        )

    @staticmethod
    async def _suppressed(session: AsyncSession, user: User, external_id: str) -> bool:
        return (
            await session.scalar(
                select(FinanceSuppression.id).where(
                    FinanceSuppression.user_id == user.id,
                    FinanceSuppression.source == EntrySource.OPEN_FINANCE,
                    FinanceSuppression.external_id == external_id,
                )
            )
        ) is not None

    @staticmethod
    def _category(transaction: RawTransaction) -> str:
        category = map_category(transaction.category)
        return str(
            category if category.value != "Outros" else guess_category(transaction.description)
        )

    @staticmethod
    def _occurred_at(value: date, timezone: str) -> datetime:
        return datetime.combine(value, time.min, ZoneInfo(timezone)).astimezone(UTC)

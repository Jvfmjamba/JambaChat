import uuid

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jambachat.expenses.models import Expense
from jambachat.finance.models import ExpenseLink, FinanceConnection, FinanceSuppression
from jambachat.users.models import User


async def _user(session: AsyncSession) -> User:
    user = User(phone_hash=uuid.uuid4().hex, phone="5511999999999")
    session.add(user)
    await session.flush()
    return user


@pytest.mark.asyncio
async def test_connection_encrypts_fields_and_hashes_item_ref(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker() as session:
        user = await _user(session)
        connection = FinanceConnection(
            user_id=user.id,
            item_ref="item-123",
            institution="Banco Exemplo",
        )
        session.add(connection)
        await session.commit()

        stored = (
            await session.execute(
                text("SELECT item_ref, institution FROM finance_connections"),
            )
        ).one()

    assert connection.item_ref_hash
    assert connection.item_ref_hash != "item-123"
    assert "item-123" not in stored.item_ref
    assert "Banco Exemplo" not in stored.institution


@pytest.mark.asyncio
async def test_item_ref_hash_is_unique_globally(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker() as session:
        first = await _user(session)
        second = await _user(session)
        session.add_all(
            [
                FinanceConnection(
                    user_id=first.id,
                    item_ref="item-123",
                    institution="Banco A",
                ),
                FinanceConnection(
                    user_id=second.id,
                    item_ref="item-123",
                    institution="Banco B",
                ),
            ]
        )

        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
async def test_suppression_is_unique_per_user_source_and_external_id(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker() as session:
        user = await _user(session)
        session.add_all(
            [
                FinanceSuppression(
                    user_id=user.id, source="open_finance", external_id="transaction-1"
                ),
                FinanceSuppression(
                    user_id=user.id, source="open_finance", external_id="transaction-1"
                ),
            ]
        )

        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
async def test_user_deletion_cascades_finance_models(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker() as session:
        await session.execute(text("PRAGMA foreign_keys = ON"))
        user = await _user(session)
        imported = Expense(
            user_id=user.id,
            amount_cents=100,
            description="Importado",
            category="Outros",
        )
        matched = Expense(
            user_id=user.id,
            amount_cents=100,
            description="Manual",
            category="Outros",
        )
        session.add_all(
            [
                imported,
                matched,
                FinanceConnection(
                    user_id=user.id, item_ref="item-123", institution="Banco Exemplo"
                ),
                FinanceSuppression(
                    user_id=user.id, source="open_finance", external_id="transaction-1"
                ),
            ]
        )
        await session.flush()
        session.add(
            ExpenseLink(
                user_id=user.id,
                imported_expense_id=imported.id,
                matched_expense_id=matched.id,
            )
        )
        await session.commit()

        await session.execute(delete(User).where(User.id == user.id))
        await session.commit()

        assert not (await session.scalars(select(FinanceConnection))).all()
        assert not (await session.scalars(select(ExpenseLink))).all()
        assert not (await session.scalars(select(FinanceSuppression))).all()

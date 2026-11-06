from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jambachat.conversation.commands import BANK_EMPTY_TEXT, handle_command
from jambachat.finance.errors import FinanceError
from jambachat.finance.provider import ItemInfo
from jambachat.users.models import User
from tests.fakes import FakeFinanceProvider


class FailingDeleteFinanceProvider(FakeFinanceProvider):
    async def delete_item(self, item_ref: str) -> None:
        raise FinanceError


async def _user(session: AsyncSession, phone: str) -> User:
    user = User(phone=phone, phone_hash=phone)
    session.add(user)
    await session.flush()
    return user


async def test_banco_lists_only_own_connections_and_requires_confirmation(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    provider = FakeFinanceProvider(
        items={
            "item-1": ItemInfo("Banco Azul", "UPDATED"),
            "item-2": ItemInfo("Banco Verde", "UPDATED"),
        }
    )
    async with sessionmaker() as session:
        user = await _user(session, "1")
        other = await _user(session, "2")
        assert await handle_command(session, user, "/banco item-1", finance_provider=provider)
        assert await handle_command(session, other, "/banco item-2", finance_provider=provider)

        listed = await handle_command(session, user, "/banco", finance_provider=provider)
        assert listed is not None
        assert "Banco Azul" in listed
        assert "Banco Verde" not in listed
        connection_id = listed.rsplit("Id: ", 1)[1]

        prompt = await handle_command(
            session, user, f"/banco remover {connection_id}", finance_provider=provider
        )
        assert prompt == "Para desconectar, mande: /banco remover <id> confirmar"
        assert provider.deleted_items == []

        deleted = await handle_command(
            session, user, f"/banco remover {connection_id} confirmar", finance_provider=provider
        )
        assert deleted == "Banco desconectado. Seus gastos importados foram mantidos."
        assert provider.deleted_items == ["item-1"]
        assert (
            await handle_command(session, user, "/banco", finance_provider=provider)
            == BANK_EMPTY_TEXT
        )


async def test_commands_accept_uppercase_confirmation_arguments(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker() as session:
        user = await _user(session, "1")

        await handle_command(session, user, "/atalho")
        assert user.wallet_token_hash is not None
        assert await handle_command(session, user, "/atalho Desligar")
        assert user.wallet_token_hash is None

        assert await handle_command(session, user, "/apagar-meus-dados Confirmar")
        await session.flush()
        assert await session.scalar(select(User).where(User.id == user.id)) is None


async def test_banco_remover_warns_when_aggregator_deletion_fails(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    provider = FailingDeleteFinanceProvider(items={"item-1": ItemInfo("Banco Azul", "UPDATED")})
    async with sessionmaker() as session:
        user = await _user(session, "1")
        assert await handle_command(session, user, "/banco item-1", finance_provider=provider)
        listed = await handle_command(session, user, "/banco", finance_provider=provider)
        assert listed is not None
        connection_id = listed.rsplit("Id: ", 1)[1]

        assert (
            await handle_command(
                session,
                user,
                f"/banco remover {connection_id} confirmar",
                finance_provider=provider,
            )
            == "Banco desconectado. A remoção no agregador falhou."
        )
        assert (
            await handle_command(session, user, "/banco", finance_provider=provider)
            == BANK_EMPTY_TEXT
        )

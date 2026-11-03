from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from jambachat.config import Settings
from jambachat.conversation.models import Message
from jambachat.expenses.models import Expense
from jambachat.reminders.models import Reminder
from jambachat.security.crypto import get_cipher
from jambachat.users.models import User


async def get_or_create_user(
    session: AsyncSession, phone: str, name: str | None, settings: Settings
) -> User:
    phone_hash = get_cipher().blind_index(phone)
    query = select(User).where(User.phone_hash == phone_hash)
    user = await session.scalar(query)
    if user is None:
        user = User(
            phone=phone,
            phone_hash=phone_hash,
            name=name,
            timezone=settings.default_timezone,
            is_admin=phone in settings.admin_phones,
        )
        try:
            async with session.begin_nested():
                session.add(user)
        except IntegrityError:
            user = (await session.scalars(query)).one()
    elif name and user.name != name:
        user.name = name
    return user


async def delete_user_data(session: AsyncSession, user: User) -> None:
    for model in (Message, Reminder, Expense):
        await session.execute(delete(model).where(model.user_id == user.id))
    await session.delete(user)

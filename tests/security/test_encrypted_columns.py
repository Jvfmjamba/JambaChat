from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from jambachat.conversation.models import Direction, Message, Role
from jambachat.db.models import Base
from jambachat.security.crypto import get_cipher
from jambachat.users.models import User


async def test_sensitive_columns_are_stored_encrypted() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    from sqlalchemy.ext.asyncio import async_sessionmaker

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        phone = "5511999990000"
        user = User(phone=phone, phone_hash=get_cipher().blind_index(phone), name="João")
        session.add(user)
        await session.flush()
        session.add(
            Message(
                user_id=user.id,
                direction=Direction.INBOUND,
                role=Role.USER,
                content={"text": "gastei 42 no mercado"},
            )
        )
        await session.commit()

    async with engine.connect() as conn:
        raw_user = (await conn.execute(text("SELECT phone, name FROM users"))).one()
        raw_message = (await conn.execute(text("SELECT content FROM messages"))).scalar_one()

    assert phone not in raw_user.phone
    assert "João" not in raw_user.name
    assert "mercado" not in raw_message

    async with session_factory() as session:
        stored = (await session.execute(Message.__table__.select())).one()
        loaded_user = await session.get(User, user.id)

    assert loaded_user is not None
    assert loaded_user.phone == phone
    assert loaded_user.name == "João"
    assert stored.content == {"text": "gastei 42 no mercado"}
    await engine.dispose()

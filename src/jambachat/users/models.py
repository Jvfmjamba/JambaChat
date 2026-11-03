from datetime import datetime

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from jambachat.db.base import Base, CreatedAt, UUIDPrimaryKey
from jambachat.security.types import EncryptedText


class User(UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "users"

    phone_hash: Mapped[str] = mapped_column(String(64), unique=True)
    phone: Mapped[str] = mapped_column(EncryptedText)
    name: Mapped[str | None] = mapped_column(EncryptedText)
    timezone: Mapped[str] = mapped_column(String(64), default="America/Sao_Paulo")
    is_admin: Mapped[bool] = mapped_column(default=False)
    last_inbound_at: Mapped[datetime | None]
    context_cleared_at: Mapped[datetime | None]

from datetime import datetime

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from jambachat.db.base import Base, CreatedAt, UUIDPrimaryKey


class User(UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "users"

    phone: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str | None] = mapped_column(String(120))
    timezone: Mapped[str] = mapped_column(String(64), default="America/Sao_Paulo")
    is_admin: Mapped[bool] = mapped_column(default=False)
    last_inbound_at: Mapped[datetime | None]

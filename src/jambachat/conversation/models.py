import uuid
from enum import StrEnum
from typing import Any

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from jambachat.db.base import Base, CreatedAt, UUIDPrimaryKey
from jambachat.security.types import EncryptedJSON


class Direction(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class Role(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class Message(UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_user_id_created_at", "user_id", "created_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    wa_message_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    direction: Mapped[Direction] = mapped_column(String(16))
    role: Mapped[Role] = mapped_column(String(16))
    content: Mapped[dict[str, Any]] = mapped_column(EncryptedJSON)

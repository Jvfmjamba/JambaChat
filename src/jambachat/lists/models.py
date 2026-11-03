import uuid

from sqlalchemy import ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from jambachat.db.base import Base, CreatedAt, UUIDPrimaryKey
from jambachat.security.types import EncryptedText


class ListItem(UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "list_items"
    __table_args__ = (Index("ix_list_items_user_id_created_at", "user_id", "created_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    list_name: Mapped[str] = mapped_column(EncryptedText)
    text: Mapped[str] = mapped_column(EncryptedText)
    done: Mapped[bool] = mapped_column(default=False)

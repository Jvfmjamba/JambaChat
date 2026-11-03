import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, validates

from jambachat.db.base import Base, CreatedAt, UUIDPrimaryKey
from jambachat.security.crypto import get_cipher
from jambachat.security.types import EncryptedText


class FinanceConnection(UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "finance_connections"
    __table_args__ = (
        Index("ix_finance_connections_status_last_synced_at", "status", "last_synced_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(32), default="pluggy")
    item_ref_hash: Mapped[str] = mapped_column(String(64), unique=True)
    item_ref: Mapped[str] = mapped_column(EncryptedText)
    institution: Mapped[str] = mapped_column(EncryptedText)
    status: Mapped[str] = mapped_column(String(16), default="active")
    last_synced_at: Mapped[datetime | None]
    last_error: Mapped[str | None] = mapped_column(String(64))
    reconnect_notified_at: Mapped[datetime | None]
    last_manual_sync_at: Mapped[datetime | None]

    @validates("item_ref")
    def _set_item_ref_hash(self, key: str, item_ref: str) -> str:
        self.item_ref_hash = get_cipher().blind_index(item_ref)
        return item_ref


class ExpenseLink(UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "expense_links"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    imported_expense_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("expenses.id", ondelete="CASCADE"), unique=True
    )
    matched_expense_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("expenses.id", ondelete="CASCADE")
    )


class FinanceSuppression(UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "finance_suppressions"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "source", "external_id", name="uq_finance_suppressions_external"
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    source: Mapped[str] = mapped_column(String(16))
    external_id: Mapped[str] = mapped_column(String(128))

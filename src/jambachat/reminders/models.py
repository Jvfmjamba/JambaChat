import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from jambachat.db.base import Base, CreatedAt, UUIDPrimaryKey
from jambachat.security.types import EncryptedText


class ReminderStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    CANCELLED = "cancelled"
    FAILED = "failed"


class Reminder(UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "reminders"
    __table_args__ = (Index("ix_reminders_status_due_at", "status", "due_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    text: Mapped[str] = mapped_column(EncryptedText)
    due_at: Mapped[datetime]
    recurrence: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[ReminderStatus] = mapped_column(String(16), default=ReminderStatus.PENDING)
    attempts: Mapped[int] = mapped_column(default=0)
    sent_at: Mapped[datetime | None]
    next_attempt_at: Mapped[datetime | None]

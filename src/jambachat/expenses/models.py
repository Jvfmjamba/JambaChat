import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from jambachat.db.base import Base, CreatedAt, UUIDPrimaryKey, utcnow


class EntryKind(StrEnum):
    EXPENSE = "expense"
    INCOME = "income"


class EntrySource(StrEnum):
    MANUAL = "manual"
    OPEN_FINANCE = "open_finance"
    WALLET = "wallet"


class Category(StrEnum):
    ALIMENTACAO = "Alimentação"
    MERCADO = "Mercado"
    TRANSPORTE = "Transporte"
    MORADIA = "Moradia"
    SAUDE = "Saúde"
    LAZER = "Lazer"
    EDUCACAO = "Educação"
    ASSINATURAS = "Assinaturas"
    COMPRAS = "Compras"
    CONTAS = "Contas"
    OUTROS = "Outros"


class Expense(UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "expenses"
    __table_args__ = (
        Index("ix_expenses_user_id_occurred_at", "user_id", "occurred_at"),
        UniqueConstraint("user_id", "source", "external_id", name="uq_expenses_external"),
        CheckConstraint("amount_cents > 0", name="amount_positive"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[EntryKind] = mapped_column(String(16), default=EntryKind.EXPENSE)
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), default="BRL")
    description: Mapped[str] = mapped_column(String(255))
    category: Mapped[str] = mapped_column(String(32), default=Category.OUTROS)
    occurred_at: Mapped[datetime] = mapped_column(default=utcnow)
    source: Mapped[EntrySource] = mapped_column(String(16), default=EntrySource.MANUAL)
    external_id: Mapped[str | None] = mapped_column(String(128))

import uuid
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from jambachat.db.base import Base, CreatedAt, UUIDPrimaryKey, utcnow
from jambachat.security.types import EncryptedText


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
    PETS = "Pets"
    VIAGEM = "Viagem"
    SALARIO = "Salário"
    INVESTIMENTOS = "Investimentos"
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
    description: Mapped[str] = mapped_column(EncryptedText)
    category: Mapped[str] = mapped_column(String(32), default=Category.OUTROS)
    occurred_at: Mapped[datetime] = mapped_column(default=utcnow)
    source: Mapped[EntrySource] = mapped_column(String(16), default=EntrySource.MANUAL)
    external_id: Mapped[str | None] = mapped_column(String(128))


TOTAL_BUDGET = "Total"


class Budget(UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "budgets"
    __table_args__ = (
        UniqueConstraint("user_id", "category", name="uq_budgets_user_category"),
        CheckConstraint("amount_cents > 0", name="amount_positive"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    category: Mapped[str] = mapped_column(String(32))
    amount_cents: Mapped[int] = mapped_column(BigInteger)


class RecurringEntry(UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "recurring_entries"
    __table_args__ = (
        Index("ix_recurring_entries_active_next_run_on", "active", "next_run_on"),
        CheckConstraint("amount_cents > 0", name="amount_positive"),
        CheckConstraint("day_of_month BETWEEN 1 AND 31", name="valid_day"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[EntryKind] = mapped_column(String(16), default=EntryKind.EXPENSE)
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    description: Mapped[str] = mapped_column(EncryptedText)
    category: Mapped[str] = mapped_column(String(32), default=Category.OUTROS)
    day_of_month: Mapped[int]
    next_run_on: Mapped[date]
    active: Mapped[bool] = mapped_column(default=True)

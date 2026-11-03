"""criar tabelas iniciais

Revision ID: 867de9a58d46
Revises:
Create Date: 2026-11-03 05:23:25.668860+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "867de9a58d46"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _id_and_created_at() -> list[sa.Column[object]]:
    return [
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    ]


def _user_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["user_id"], ["users.id"], name=op.f(f"fk_{table}_user_id_users"), ondelete="CASCADE"
    )


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("phone_hash", sa.String(length=64), nullable=False),
        sa.Column("phone", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("is_admin", sa.Boolean(), nullable=False),
        sa.Column("last_inbound_at", sa.DateTime(timezone=True), nullable=True),
        *_id_and_created_at(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("phone_hash", name=op.f("uq_users_phone_hash")),
    )

    op.create_table(
        "expenses",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("external_id", sa.String(length=128), nullable=True),
        *_id_and_created_at(),
        sa.CheckConstraint("amount_cents > 0", name=op.f("ck_expenses_amount_positive")),
        _user_fk("expenses"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_expenses")),
        sa.UniqueConstraint("user_id", "source", "external_id", name="uq_expenses_external"),
    )
    op.create_index("ix_expenses_user_id_occurred_at", "expenses", ["user_id", "occurred_at"])

    op.create_table(
        "messages",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("wa_message_id", sa.String(length=128), nullable=True),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        *_id_and_created_at(),
        _user_fk("messages"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_messages")),
        sa.UniqueConstraint("wa_message_id", name=op.f("uq_messages_wa_message_id")),
    )
    op.create_index("ix_messages_user_id_created_at", "messages", ["user_id", "created_at"])

    op.create_table(
        "reminders",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recurrence", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        *_id_and_created_at(),
        _user_fk("reminders"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reminders")),
    )
    op.create_index("ix_reminders_status_due_at", "reminders", ["status", "due_at"])


def downgrade() -> None:
    op.drop_index("ix_reminders_status_due_at", table_name="reminders")
    op.drop_table("reminders")
    op.drop_index("ix_messages_user_id_created_at", table_name="messages")
    op.drop_table("messages")
    op.drop_index("ix_expenses_user_id_occurred_at", table_name="expenses")
    op.drop_table("expenses")
    op.drop_table("users")

"""criar modelos open finance

Revision ID: f8c2a1d4e6b7
Revises: b9d1f3a5c7e2
Create Date: 2026-11-03 23:00:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f8c2a1d4e6b7"
down_revision: str | Sequence[str] | None = "b9d1f3a5c7e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "finance_connections",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("item_ref_hash", sa.String(length=64), nullable=False),
        sa.Column("item_ref", sa.Text(), nullable=False),
        sa.Column("institution", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=64), nullable=True),
        sa.Column("reconnect_notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_manual_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_finance_connections_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_finance_connections")),
        sa.UniqueConstraint("item_ref_hash", name=op.f("uq_finance_connections_item_ref_hash")),
    )
    op.create_index(
        "ix_finance_connections_status_last_synced_at",
        "finance_connections",
        ["status", "last_synced_at"],
    )
    op.create_table(
        "expense_links",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("imported_expense_id", sa.Uuid(), nullable=False),
        sa.Column("matched_expense_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["imported_expense_id"],
            ["expenses.id"],
            name=op.f("fk_expense_links_imported_expense_id_expenses"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["matched_expense_id"],
            ["expenses.id"],
            name=op.f("fk_expense_links_matched_expense_id_expenses"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_expense_links_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_expense_links")),
        sa.UniqueConstraint(
            "imported_expense_id", name=op.f("uq_expense_links_imported_expense_id")
        ),
    )
    op.create_table(
        "finance_suppressions",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("external_id", sa.String(length=128), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_finance_suppressions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_finance_suppressions")),
        sa.UniqueConstraint(
            "user_id",
            "source",
            "external_id",
            name="uq_finance_suppressions_external",
        ),
    )


def downgrade() -> None:
    op.drop_table("finance_suppressions")
    op.drop_table("expense_links")
    op.drop_index("ix_finance_connections_status_last_synced_at", table_name="finance_connections")
    op.drop_table("finance_connections")

"""criar lancamentos recorrentes

Revision ID: f5b1d3e7a9c2
Revises: e3a9b5c7d1f4
Create Date: 2026-11-03 21:00:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f5b1d3e7a9c2"
down_revision: str | Sequence[str] | None = "e3a9b5c7d1f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "recurring_entries",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("day_of_month", sa.Integer(), nullable=False),
        sa.Column("next_run_on", sa.Date(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("amount_cents > 0", name=op.f("ck_recurring_entries_amount_positive")),
        sa.CheckConstraint(
            "day_of_month BETWEEN 1 AND 31", name=op.f("ck_recurring_entries_valid_day")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_recurring_entries_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recurring_entries")),
    )
    op.create_index(
        "ix_recurring_entries_active_next_run_on",
        "recurring_entries",
        ["active", "next_run_on"],
    )


def downgrade() -> None:
    op.drop_index("ix_recurring_entries_active_next_run_on", table_name="recurring_entries")
    op.drop_table("recurring_entries")

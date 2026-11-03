"""criar listas

Revision ID: e3a9b5c7d1f4
Revises: d8f2c6a4e1b7
Create Date: 2026-11-03 20:00:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e3a9b5c7d1f4"
down_revision: str | Sequence[str] | None = "d8f2c6a4e1b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "list_items",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("list_name", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("done", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_list_items_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_list_items")),
    )
    op.create_index("ix_list_items_user_id_created_at", "list_items", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_list_items_user_id_created_at", table_name="list_items")
    op.drop_table("list_items")

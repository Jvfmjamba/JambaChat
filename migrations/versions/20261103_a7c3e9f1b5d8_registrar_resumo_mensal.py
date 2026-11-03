"""registrar resumo mensal

Revision ID: a7c3e9f1b5d8
Revises: f5b1d3e7a9c2
Create Date: 2026-11-03 22:00:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7c3e9f1b5d8"
down_revision: str | Sequence[str] | None = "f5b1d3e7a9c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("last_summary_month", sa.String(length=7), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("last_summary_month")

"""adicionar limpeza de contexto

Revision ID: 3f1c9a7e2b44
Revises: 867de9a58d46
Create Date: 2026-11-03 15:00:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3f1c9a7e2b44"
down_revision: str | Sequence[str] | None = "867de9a58d46"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("context_cleared_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("context_cleared_at")

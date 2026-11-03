"""adicionar reagendamento de lembretes

Revision ID: 8b2d4f6a1c90
Revises: 3f1c9a7e2b44
Create Date: 2026-11-03 18:00:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8b2d4f6a1c90"
down_revision: str | Sequence[str] | None = "3f1c9a7e2b44"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("reminders") as batch:
        batch.add_column(sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("reminders") as batch:
        batch.drop_column("next_attempt_at")

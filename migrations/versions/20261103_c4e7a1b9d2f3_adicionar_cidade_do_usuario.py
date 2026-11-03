"""adicionar cidade do usuario

Revision ID: c4e7a1b9d2f3
Revises: 8b2d4f6a1c90
Create Date: 2026-11-03 18:00:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4e7a1b9d2f3"
down_revision: str | Sequence[str] | None = "8b2d4f6a1c90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("home_city", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("home_city")

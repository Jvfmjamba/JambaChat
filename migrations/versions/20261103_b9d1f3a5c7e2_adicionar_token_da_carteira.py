"""adicionar token da carteira

Revision ID: b9d1f3a5c7e2
Revises: a7c3e9f1b5d8
Create Date: 2026-11-03 20:00:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b9d1f3a5c7e2"
down_revision: str | Sequence[str] | None = "a7c3e9f1b5d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("wallet_token_hash", sa.String(length=64), nullable=True))
        batch.create_unique_constraint("uq_users_wallet_token_hash", ["wallet_token_hash"])


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("uq_users_wallet_token_hash", type_="unique")
        batch.drop_column("wallet_token_hash")

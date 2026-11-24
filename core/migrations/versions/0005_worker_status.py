"""worker status: running | waiting (alive, not taking messages)

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-24 16:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("workers") as b:
        b.add_column(sa.Column("status", sa.String(length=16), nullable=False, server_default="running"))
        b.add_column(sa.Column("detail", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("workers") as b:
        b.drop_column("detail")
        b.drop_column("status")

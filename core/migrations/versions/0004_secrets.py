"""encrypted secrets (local secret store)

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-24 14:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "secrets",
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("hint", sa.String(length=16), nullable=False),
        sa.Column("updated_at", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("name", name=op.f("pk_secrets")),
    )


def downgrade() -> None:
    op.drop_table("secrets")

"""job_items.claimed_until: a lease so one posting of a job is labeled by one worker at a time

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-25 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("job_items") as b:
        b.add_column(sa.Column("claimed_until", sa.String(length=32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("job_items") as b:
        b.drop_column("claimed_until")

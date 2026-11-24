"""worker heartbeats and per-version label status

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-24 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workers",
        sa.Column("worker_id", sa.String(length=128), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("concurrency", sa.Integer(), nullable=False),
        sa.Column("inflight", sa.Integer(), nullable=False),
        sa.Column("processed", sa.Integer(), nullable=False),
        sa.Column("failed", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.String(length=32), nullable=False),
        sa.Column("last_seen", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("worker_id", name=op.f("pk_workers")),
    )
    op.create_table(
        "label_status",
        sa.Column("posting_id", sa.String(length=64), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("posting_id", "policy_version", name=op.f("pk_label_status")),
    )
    op.create_index("ix_label_status_version_status", "label_status", ["policy_version", "status"])
    # Backfill from what already exists: stored labels are done (or failed), queued job items
    # without a label are queued.
    op.execute(
        "INSERT INTO label_status (posting_id, policy_version, status, run_id, error, updated_at) "
        "SELECT job_id, policy_version, CASE WHEN tier = 'failed' THEN 'failed' ELSE 'done' END, NULL, '', created_at "
        "FROM labels"
    )
    op.execute(
        "INSERT INTO label_status (posting_id, policy_version, status, run_id, error, updated_at) "
        "SELECT ji.posting_id, j.policy_version, 'queued', MAX(j.id), '', MAX(j.created_at) "
        "FROM job_items ji JOIN jobs j ON j.id = ji.job_id "
        "WHERE ji.status = 'pending' AND NOT EXISTS ("
        "  SELECT 1 FROM labels l WHERE l.job_id = ji.posting_id AND l.policy_version = j.policy_version) "
        "GROUP BY ji.posting_id, j.policy_version"
    )


def downgrade() -> None:
    op.drop_index("ix_label_status_version_status", table_name="label_status")
    op.drop_table("label_status")
    op.drop_table("workers")

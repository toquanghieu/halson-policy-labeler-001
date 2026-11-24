"""Relational schema (SQLAlchemy Core). Alembic migrations are generated from this metadata.

Portable across Postgres (local compose, Aurora in production) and SQLite (tests, no-infra dev):
no dialect-specific JSON operators. Anything we filter on is a real column or a real table.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
)

metadata = MetaData(
    naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_name)s",
        "pk": "pk_%(table_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    }
)

postings = Table(
    "postings",
    metadata,
    Column("job_id", String(64), primary_key=True),
    Column("title", Text, nullable=False, default=""),  # denormalised for list views and search
    Column("location", Text, nullable=False, default=""),
    Column("data", Text, nullable=False),  # Posting JSON
    Column("gold", Integer),  # EMSCAD `fraudulent`; NULL for new submissions
    Column("is_eval", Boolean, nullable=False, default=False),
    Column("created_at", String(32), nullable=False),
)
Index("ix_postings_created_at", postings.c.created_at)

policies = Table(
    "policies",
    metadata,
    Column("version", String(32), primary_key=True),
    Column("source", Text, nullable=False),  # YAML exactly as the analyst wrote it
    Column("status", String(16), nullable=False),  # draft | published | retired
    Column("note", Text, nullable=False, default=""),
    Column("created_at", String(32), nullable=False),
    Column("published_at", String(32)),
)

labels = Table(
    "labels",
    metadata,
    Column("job_id", String(64), primary_key=True),
    Column("policy_version", String(64), primary_key=True),  # also "<version>@<tag>" for ablation runs
    Column("label", String(16), nullable=False),
    Column("confidence", Float, nullable=False),
    Column("tier", String(16), nullable=False),
    Column("cost_usd", Float, nullable=False),
    Column("latency_ms", Float, nullable=False),
    Column("result", Text, nullable=False),  # LabelResult JSON without the heavy trace
    Column("trace_key", Text, nullable=False),  # object-store key of the full trace
    Column("created_at", String(32), nullable=False),
)
Index("ix_labels_version_label", labels.c.policy_version, labels.c.label)

label_sections = Table(
    "label_sections",
    metadata,
    Column("job_id", String(64), primary_key=True),
    Column("policy_version", String(64), primary_key=True),
    Column("section", String(16), primary_key=True),
)
Index("ix_label_sections_version_section", label_sections.c.policy_version, label_sections.c.section)

jobs = Table(
    "jobs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("kind", String(16), nullable=False),  # batch | relabel
    Column("policy_version", String(64), nullable=False),
    Column("queue", String(16), nullable=False, default="live"),  # live | backfill
    Column("status", String(16), nullable=False),  # queued | running | done | failed
    Column("total", Integer, nullable=False),
    Column("detail", Text, nullable=False, default=""),
    Column("created_at", String(32), nullable=False),
    Column("finished_at", String(32)),
)

# One row per posting in a job. Workers flip `pending` to done/failed exactly once, so a
# redelivered queue message never double-counts, and progress is a COUNT, not a counter.
job_items = Table(
    "job_items",
    metadata,
    Column("job_id", Integer, ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True),
    Column("posting_id", String(64), primary_key=True),
    Column("status", String(16), nullable=False, default="pending"),  # pending | done | failed
    # Lease: the worker labeling this item holds it until then (UTC ISO). A redelivered message
    # meanwhile is left on the queue; a crashed worker's lease simply runs out.
    Column("claimed_until", String(32), nullable=True),
)
Index("ix_job_items_job_status", job_items.c.job_id, job_items.c.status)

# Transactional outbox: a PostingLabeled event is written in the same transaction as the label.
# The marketplace reads it (GET /api/events); in production a relay publishes it to EventBridge.
events = Table(
    "events",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("type", String(32), nullable=False),
    Column("job_id", String(64), nullable=False),
    Column("policy_version", String(64), nullable=False),
    Column("payload", Text, nullable=False),
    Column("created_at", String(32), nullable=False),
)

# Run-time settings an operator changes from the UI (models per tier, escalation thresholds,
# backfill window). Environment variables give the defaults; a row here overrides one key.
app_settings = Table(
    "app_settings",
    metadata,
    Column("key", String(64), primary_key=True),
    Column("value", Text, nullable=False),  # JSON
    Column("updated_at", String(32), nullable=False),
)

# Worker liveness (labeler_worker.heartbeat): one row per running worker process, refreshed every
# few seconds and removed on clean shutdown. A row older than ~30 s is a worker that died.
workers = Table(
    "workers",
    metadata,
    Column("worker_id", String(128), primary_key=True),
    Column("role", String(16), nullable=False),  # standalone | embedded
    Column("concurrency", Integer, nullable=False),
    Column("inflight", Integer, nullable=False, default=0),
    Column("processed", Integer, nullable=False, default=0),
    Column("failed", Integer, nullable=False, default=0),
    Column("started_at", String(32), nullable=False),
    Column("last_seen", String(32), nullable=False),
    # running | waiting (alive but not taking messages, e.g. no API key yet); detail says why
    Column("status", String(16), nullable=False, default="running", server_default="running"),
    Column("detail", Text, nullable=False, default="", server_default=""),
)

# Where each (posting, policy version) stands: queued -> labeling -> done | failed. Written by the
# producer (queued), the worker (labeling), save_label (done / failed) and the DLQ path (failed),
# so "what is still in flight under v2" or "what failed" is one indexed query.
label_status = Table(
    "label_status",
    metadata,
    Column("posting_id", String(64), primary_key=True),
    Column("policy_version", String(64), primary_key=True),
    Column("status", String(16), nullable=False),  # queued | labeling | done | failed
    Column("run_id", Integer),  # the job that last touched it
    Column("error", Text, nullable=False, default=""),
    Column("updated_at", String(32), nullable=False),
)
Index("ix_label_status_version_status", label_status.c.policy_version, label_status.c.status)

# API keys entered on the Settings page, encrypted with the secrets master key (local and compose
# only; on AWS the secret store is Secrets Manager and this table stays empty). See DESIGN.md.
secrets = Table(
    "secrets",
    metadata,
    Column("name", String(64), primary_key=True),
    Column("ciphertext", Text, nullable=False),
    Column("hint", String(16), nullable=False),  # last 4 characters, for "…abcd" in the UI
    Column("updated_at", String(32), nullable=False),
)

"""Shared core of the policy labeler, used by the API (backend/) and the worker (worker/).

config.py, errors.py   settings and domain errors
labeling/              the agent: policy, prompt, evidence verification, router, metrics
infra/                 adapters: db (Postgres/SQLite + Alembic), queue (SQS/memory), storage
                       (S3/local), llm (provider registry), secrets, telemetry
services/              use cases shared by both processes (label, publish, re-label, settings)
bootstrap.py           composition root: builds the service from settings
"""

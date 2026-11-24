"""Composition root: the one place that picks concrete implementations and wires them together.

| port          | tests / `make dev`     | docker compose       | AWS (DESIGN.md)    |
|---------------|------------------------|----------------------|--------------------|
| database      | SQLite file            | Postgres 16          | Aurora Postgres    |
| work queue    | in-memory              | ElasticMQ (SQS API)  | SQS + DLQ          |
| object store  | directory on disk      | SeaweedFS (S3 API)   | S3                 |
| model         | scripted fake / CLI    | Claude / OpenAI / OpenAI-compatible (per tier) | Bedrock |
| secrets       | env                    | encrypted in Postgres (Fernet) | Secrets Manager |

The API (labeler_api.main), the worker (labeler_worker), the scripts and the tests all build through here.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path

from labeler.config import Settings
from labeler.infra.db import DB, make_engine
from labeler.infra.db.migrate import upgrade
from labeler.infra.llm.port import LLMProvider
from labeler.infra.llm.registry import ProviderRegistry
from labeler.infra.queue.memory_adapter import MemoryQueue
from labeler.infra.queue.port import WorkQueue
from labeler.infra.queue.sqs_adapter import SQSQueue
from labeler.infra.secrets.aws_adapter import AwsSecrets
from labeler.infra.secrets.db_adapter import EncryptedDbSecrets
from labeler.infra.secrets.env_adapter import EnvSecrets
from labeler.infra.secrets.port import SecretStore
from labeler.infra.storage.local_adapter import LocalStore
from labeler.infra.storage.port import ObjectStore
from labeler.infra.storage.s3_adapter import S3Store
from labeler.labeling.agent import LabelingAgent
from labeler.labeling.router import load_router
from labeler.services.labeling import Service

log = logging.getLogger(__name__)

ROUTER_KEY = "models/router.joblib"


def build_secrets(settings: Settings, db: DB) -> SecretStore:
    """API keys: environment only, encrypted in the database (local/compose), or AWS Secrets Manager."""
    if settings.secrets_backend == "aws":
        return AwsSecrets(settings.secrets_prefix, settings.aws_region, settings.secrets_endpoint_url)
    if settings.secrets_backend == "db":
        return EncryptedDbSecrets(db, settings.secrets_key, settings.secrets_key_file)
    return EnvSecrets()


def _with_retry(what: str, fn: Callable[[], None], attempts: int = 60, delay_s: float = 2.0) -> None:
    """Dependencies start in any order under compose: wait for them instead of crashing. Up to
    ~2 minutes, because SeaweedFS reloading a full volume after a Docker restart takes more than 30 s."""
    for i in range(1, attempts + 1):
        try:
            fn()
            return
        except Exception as e:  # connection errors come in many types
            if i == attempts:
                raise
            log.info("waiting for %s (%s)", what, type(e).__name__)
            time.sleep(delay_s)


def build_store(settings: Settings) -> ObjectStore:
    if settings.storage_backend == "s3":
        store = S3Store(settings.s3_bucket, settings.s3_endpoint_url, settings.aws_region)
        _with_retry("object store", store.ensure_bucket)
        return store
    return LocalStore(settings.data_dir / "objects")


def build_queue(settings: Settings) -> WorkQueue:
    if settings.queue_backend == "sqs":
        q = SQSQueue(
            {"sync": settings.queue_sync, "live": settings.queue_live, "backfill": settings.queue_backfill},
            settings.sqs_endpoint_url,
            settings.aws_region,
        )
        _with_retry("queue", lambda: q.ensure(visibility_timeout_s=settings.queue_visibility_timeout_s))
        return q
    return MemoryQueue(visibility_timeout_s=float(settings.queue_visibility_timeout_s))


def build_db(settings: Settings, store: ObjectStore | None = None, *, migrate: bool = True) -> DB:
    if settings.db_url.startswith("sqlite"):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
    engine = make_engine(settings.db_url, pool_size=settings.max_concurrency + 2)
    if migrate:
        upgrade(engine)
    return DB(engine, store or build_store(settings))


def router_path(settings: Settings, store: ObjectStore) -> Path:
    """The router is trained once (scripts.data router) and published to the object store,
    so every API and worker container loads the same model. Cached on local disk."""
    local = settings.router_path
    if not local.exists():
        blob = store.get_bytes(ROUTER_KEY)
        if blob is not None:
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(blob)
    return local


def build_service(
    settings: Settings,
    provider: LLMProvider | None = None,
    *,
    queue: WorkQueue | None = None,
    store: ObjectStore | None = None,
) -> Service:
    store = store or build_store(settings)
    db = build_db(settings, store)
    router = load_router(str(router_path(settings, store)))
    if router is None:
        log.warning("no router model: escalation runs without the router-disagreement trigger")
    secrets = build_secrets(settings, db)
    # Providers are looked up per call by name, with the key and URL current at that moment.
    registry = ProviderRegistry(lambda: settings, secrets, override=provider)
    agent = LabelingAgent(registry, settings, router)
    svc = Service(db, agent, queue or build_queue(settings), settings.max_concurrency)
    registry.settings = svc.settings  # run-time overrides (Settings page) decide base URLs too
    svc.registry, svc.secrets = registry, secrets
    v1 = settings.policies_dir / "v1.yaml"
    if v1.exists():
        svc.seed_policy_if_empty(v1.read_text())
    return svc

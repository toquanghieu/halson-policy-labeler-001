"""Runtime settings, read from environment variables or `.env` (see .env.example)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]  # core/labeler/config.py -> repo root
STORE_MARGIN_S = 30  # seconds kept between the labeling deadline and the queue's visibility timeout

Effort = Literal["low", "medium", "high", "xhigh", "max"]
# anthropic: Claude API · claude_cli: local Claude Code login (dev) · openai: OpenAI API ·
# openai_compat: any OpenAI-style endpoint (Gemini, Groq, OpenRouter, Ollama) · slm: the distilled
# student (scripts/distill.py) · fake: tests
ProviderName = Literal["anthropic", "claude_cli", "openai", "openai_compat", "slm", "fake"]


class Settings(BaseSettings):
    """Every knob of the system. Field names map to upper-case env vars (DATA_DIR, TIER1_MODEL, ...)."""

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_ignore_empty=True,  # `TIER2_MODEL=` in .env means "default", not "empty string"
        extra="ignore",  # .env also holds ANTHROPIC_API_KEY, read by the SDK itself
        frozen=True,
    )

    data_dir: Path = REPO_ROOT / "data"
    policies_dir: Path = REPO_ROOT / "policies"

    # Default provider for both tiers; each tier can override it (here or on the Settings page).
    llm_provider: ProviderName = "anthropic"
    tier1_provider: ProviderName | None = None
    tier2_provider: ProviderName | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    compat_base_url: str = "http://localhost:11434/v1"  # Ollama by default; Gemini/Groq/OpenRouter also work
    slm_base_url: str = "http://localhost:8090/v1"

    # Where API keys live (core/labeler/infra/secrets/): env (read-only), db (encrypted; local/compose),
    # aws (Secrets Manager). SECRETS_KEY is the Fernet master key for `db`; empty -> data/secrets.key.
    secrets_backend: Literal["env", "db", "aws"] = "db"
    secrets_key: str = ""
    secrets_prefix: str = "policy-labeler/"
    secrets_endpoint_url: str | None = None
    # Tier 1 reads every posting; tier 2 only sees escalations. An empty tier2_model disables escalation.
    tier1_model: str = "claude-haiku-5-5"
    tier2_model: str = "claude-opus-5-5"
    tier1_effort: Effort = "low"
    tier2_effort: Effort = "medium"

    # Escalate when tier-1 confidence is below this, or on the other triggers in agent.py.
    escalate_below_confidence: float = Field(0.6, ge=0, le=1)
    # Router risk score above which an ALLOWED from tier 1 is treated as a disagreement.
    router_disagree_threshold: float = Field(0.5, ge=0, le=1)
    max_concurrency: int = Field(8, ge=1, le=256)
    # Re-label window after publishing a version (the brief: "may require re-labeling the last 30 days"),
    # and whether publishing starts it automatically. Both are editable at run time (Settings page).
    backfill_days: int = Field(30, ge=1, le=365)
    backfill_on_publish: bool = False
    # Wall-clock budget for one posting (both tiers, retries included). Kept below the queue's
    # visibility timeout (300 s), so a slow posting ends as a failed label instead of reappearing
    # on the queue while still being worked on, and being sent to the model twice.
    label_deadline_s: float = Field(240.0, gt=0)
    # POST /api/postings waits this long for the worker's decision before answering 202 + job id.
    # Below a load balancer's usual 60 s idle timeout; a typical posting takes 5-15 s.
    sync_label_wait_s: float = Field(50.0, gt=0)
    # How long a received message stays invisible to other workers (SQS VisibilityTimeout; also
    # deploy/elasticmq.conf). Must exceed label_deadline_s plus time to store the result.
    queue_visibility_timeout_s: int = Field(300, ge=30)

    # ---- infrastructure. Defaults run everything in one process with no services:
    # SQLite, an in-memory queue drained by a worker inside the API, objects on disk.
    # docker-compose sets Postgres, SQS (ElasticMQ) and S3 (SeaweedFS); production the AWS ones.
    database_url: str = ""  # "" -> sqlite:///<data_dir>/app.db
    queue_backend: Literal["memory", "sqs"] = "memory"
    sqs_endpoint_url: str | None = None  # ElasticMQ locally; unset on AWS
    queue_sync: str = "labels-sync"
    queue_live: str = "labels-live"
    queue_backfill: str = "labels-backfill"
    storage_backend: Literal["local", "s3"] = "local"
    s3_endpoint_url: str | None = None  # SeaweedFS locally; unset on AWS
    s3_bucket: str = "labeler"
    aws_region: str = "us-east-1"
    # Run the queue consumer inside the API process (memory queue, `make dev`). In compose the
    # API only enqueues and separate `worker` containers consume.
    embedded_worker: bool = True
    metrics_port: int = 9100  # worker's Prometheus endpoint; the API serves /metrics itself

    @model_validator(mode="after")
    def _deadline_inside_visibility(self) -> Settings:
        # Storing a decision (object store + DB) needs a margin after the model calls end.
        if self.label_deadline_s > self.queue_visibility_timeout_s - STORE_MARGIN_S:
            raise ValueError(
                f"label_deadline_s ({self.label_deadline_s:g}) must be at most queue_visibility_timeout_s "
                f"({self.queue_visibility_timeout_s}) - {STORE_MARGIN_S}: otherwise a slow posting reappears "
                "on the queue while still being labeled and is sent to the model twice"
            )
        return self

    def provider_for(self, tier: int) -> str:
        return (self.tier1_provider if tier == 1 else self.tier2_provider) or self.llm_provider

    @property
    def secrets_key_file(self) -> Path:
        return self.data_dir / "secrets.key"

    @property
    def db_url(self) -> str:
        return self.database_url or f"sqlite:///{self.db_path}"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.db"

    @property
    def router_path(self) -> Path:
        return self.data_dir / "router.joblib"

    @property
    def raw_csv(self) -> Path:
        return self.data_dir / "raw" / "fake_job_postings.csv"

    @property
    def eval_ids_path(self) -> Path:
        return self.data_dir / "eval" / "eval_job_ids.txt"


@lru_cache
def get_settings() -> Settings:
    return Settings()

"""Settings an operator changes from the UI without a redeploy.

Environment variables (labeler.config.Settings) give the defaults. The Settings page writes
overrides to the `app_settings` table; the API and every worker read them through
`RuntimeConfig.current()`, cached for a few seconds, so a change reaches all workers within
`CACHE_S` without restarting anything. Infrastructure settings (database, queues, buckets)
are deliberately not here: changing those needs a deploy, not a click.
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from labeler.config import Effort, ProviderName, Settings
from labeler.errors import InvalidInput
from labeler.infra.db import DB
from labeler.infra.llm.port import PRICES
from labeler.infra.llm.registry import CATALOG

CACHE_S = 5.0


class RuntimeSettings(BaseModel):
    tier1_provider: ProviderName
    tier1_model: str = Field(min_length=1)
    tier1_effort: Effort
    tier2_provider: ProviderName
    tier2_model: str  # "" disables escalation
    tier2_effort: Effort
    escalate_below_confidence: float = Field(ge=0, le=1)
    router_disagree_threshold: float = Field(ge=0, le=1)
    backfill_days: int = Field(ge=1, le=365)
    backfill_on_publish: bool
    openai_base_url: str = Field(min_length=8)
    compat_base_url: str = Field(min_length=8)


KEYS = tuple(RuntimeSettings.model_fields)

DESCRIPTIONS = {
    "tier1_provider": "Who serves the first pass: Claude, OpenAI, an OpenAI-compatible endpoint, or the local student.",
    "tier2_provider": "Who serves escalations. Can differ from tier 1 (e.g. a free local model first, Claude on doubt).",
    "openai_base_url": "OpenAI API base URL (change only for a proxy or Azure-style gateway).",
    "compat_base_url": "Base URL of the OpenAI-compatible endpoint: Ollama, Gemini, Groq, OpenRouter, ...",
    "tier1_model": "Model that reads every posting.",
    "tier1_effort": "Reasoning effort for the first pass. Higher is slower and costs more output tokens.",
    "tier2_model": "Model for escalated postings. Empty turns escalation off.",
    "tier2_effort": "Reasoning effort for escalations.",
    "escalate_below_confidence": "Escalate when the first pass reports confidence below this. The main cost lever.",
    "router_disagree_threshold": "Escalate an ALLOWED when the router's risk score is at least this.",
    "backfill_days": "How far back a re-label after publishing reaches.",
    "backfill_on_publish": "Start that re-label automatically whenever a version is published.",
}


def _defaults(base: Settings) -> RuntimeSettings:
    values = {k: getattr(base, k) for k in KEYS}
    values["tier1_provider"], values["tier2_provider"] = base.provider_for(1), base.provider_for(2)
    return RuntimeSettings(**values)


class RuntimeConfig:
    def __init__(self, db: DB, base: Settings):
        self.db = db
        self.base = base
        self._cached: tuple[float, Settings] | None = None

    def current(self) -> Settings:
        """The effective Settings: env defaults with the stored overrides applied."""
        t = time.monotonic()
        if self._cached and t - self._cached[0] < CACHE_S:
            return self._cached[1]
        overrides = {k: v for k, v in self.db.get_app_settings().items() if k in KEYS}
        try:
            merged = RuntimeSettings(**{**_defaults(self.base).model_dump(), **overrides})
        except ValidationError:  # a bad row must not stop labeling: fall back to the env defaults
            merged = _defaults(self.base)
        effective = self.base.model_copy(update=merged.model_dump())
        self._cached = (t, effective)
        return effective

    def describe(self) -> dict[str, Any]:
        """What the Settings page shows: current values, env defaults, which keys are overridden, choices."""
        stored = self.db.get_app_settings()
        cur = self.current()
        return {
            "values": {k: getattr(cur, k) for k in KEYS},
            "defaults": _defaults(self.base).model_dump(),
            "overridden": sorted(k for k in stored if k in KEYS),
            "descriptions": DESCRIPTIONS,
            "choices": {
                "models": [m for m in PRICES if m != "fake"],
                "efforts": ["low", "medium", "high", "xhigh", "max"],
                "providers": [p for p in CATALOG if p != "fake"],
            },
            "prices_per_mtok": {
                m: {"input": p[0], "output": p[1], "cache_read": round(p[0] * p[2], 4)}
                for m, p in PRICES.items()
                if m != "fake"
            },
            "provider": self.base.llm_provider,
        }

    def update(self, patch: dict[str, Any]) -> dict[str, Any]:
        unknown = set(patch) - set(KEYS)
        if unknown:
            raise InvalidInput(f"not a run-time setting: {', '.join(sorted(unknown))}")
        merged = {**{k: getattr(self.current(), k) for k in KEYS}, **patch}
        try:
            RuntimeSettings(**merged)
        except ValidationError as e:
            raise InvalidInput(e.errors()[0]["msg"] + f" ({e.errors()[0]['loc'][0]})") from e
        defaults = _defaults(self.base).model_dump()
        # Store only what differs from the env default, so changing the env still works for the rest.
        self.db.set_app_settings({k: (None if v == defaults[k] else v) for k, v in patch.items()})
        self._cached = None
        return self.describe()

    def reset(self) -> dict[str, Any]:
        self.db.clear_app_settings()
        self._cached = None
        return self.describe()

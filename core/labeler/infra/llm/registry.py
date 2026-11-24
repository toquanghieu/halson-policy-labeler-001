"""Builds the model provider for a name, with the current base URL and API key.

The agent asks for a provider per call (tier 1 and tier 2 can be different vendors). Providers
are cached by (name, base URL, key fingerprint), so changing a key or a URL on the Settings page
takes effect on the next call, in the API and in every worker, without a restart.
"""

from __future__ import annotations

import hashlib
import shutil
import time
from collections.abc import Callable
from typing import Any

from labeler.config import Settings
from labeler.infra.llm.port import LLMError, LLMProvider, LLMRequest
from labeler.infra.secrets.port import SecretStore

CLAUDE_MODELS = ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"]

# What the Settings page shows for each provider. `free` describes the service, not a promise:
# free tiers have rate limits and change over time.
CATALOG: dict[str, dict[str, Any]] = {
    "anthropic": {
        "label": "Claude (Anthropic API)",
        "secret": "ANTHROPIC_API_KEY",
        "base_url": None,
        "models": CLAUDE_MODELS,
        "free": None,
    },
    "claude_cli": {
        "label": "Claude via the local Claude Code login (development)",
        "secret": None,
        "base_url": None,
        "models": CLAUDE_MODELS,
        "free": "uses your Claude subscription; host only (`make worker`)",
    },
    "openai": {
        "label": "OpenAI",
        "secret": "OPENAI_API_KEY",
        "base_url": "openai_base_url",
        "models": [],  # listed live by "Test connection"
        "free": None,
    },
    "openai_compat": {
        "label": "OpenAI-compatible endpoint",
        "secret": "COMPAT_API_KEY",
        "base_url": "compat_base_url",
        "models": [],
        "free": "Ollama (local) is free; Google Gemini, Groq and OpenRouter have free tiers",
        "presets": {
            "Ollama (local)": "http://localhost:11434/v1",
            "Ollama (from a container)": "http://host.docker.internal:11434/v1",
            "Google Gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
            "Groq": "https://api.groq.com/openai/v1",
            "OpenRouter": "https://openrouter.ai/api/v1",
        },
    },
    "slm": {
        "label": "Distilled student (Qwen2.5-1.5B + LoRA, local)",
        "secret": None,
        "base_url": "slm_base_url",
        "models": ["slm-qwen2.5-1.5b-distilled"],
        "free": "runs on your machine (`make slm-serve`)",
    },
}

PING_SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}


class ProviderRegistry:
    def __init__(self, settings: Callable[[], Settings], secrets: SecretStore, override: LLMProvider | None = None):
        self.settings = settings
        self.secrets = secrets
        self.override = override  # tests: one scripted provider for every name
        self._cache: dict[tuple[str, str, str], LLMProvider] = {}

    def _config(self, name: str) -> tuple[str, str | None]:
        meta = CATALOG.get(name, {})
        s = self.settings()
        url = getattr(s, meta["base_url"]) if meta.get("base_url") else ""
        key = self.secrets.get(meta["secret"]) if meta.get("secret") else None
        return url, key

    def unavailable(self, name: str) -> str | None:
        """Why `name` cannot be called right now, or None when it can. Cheap: no network call.

        Workers check this before taking messages, so a missing key leaves postings on the queue
        instead of turning each one into a failed label.
        """
        if self.override is not None or name == "fake":
            return None
        meta = CATALOG.get(name)
        if meta is None:
            return f"unknown provider {name!r}"
        if name == "claude_cli" and shutil.which("claude") is None:
            return "the `claude` command is not installed here (claude_cli works on the host only: `make worker`)"
        # The compatible endpoint's key is optional: a local Ollama needs none.
        secret = meta.get("secret")
        if secret and name != "openai_compat" and not self.secrets.get(secret):
            return f"{secret} is not set (add it on the Settings page)"
        return None

    def __call__(self, name: str) -> LLMProvider:
        if self.override is not None:
            return self.override
        url, key = self._config(name)
        fp = hashlib.sha256((key or "").encode()).hexdigest()[:12]
        cache_key = (name, url, fp)
        if cache_key not in self._cache:
            self._cache[cache_key] = self._build(name, url, key)
        return self._cache[cache_key]

    @staticmethod
    def _build(name: str, url: str, key: str | None) -> LLMProvider:
        if name == "fake":
            from labeler.infra.llm.fake_adapter import FakeProvider

            return FakeProvider()
        if name == "claude_cli":
            from labeler.infra.llm.claude_cli_adapter import ClaudeCLIProvider

            return ClaudeCLIProvider()
        if name == "anthropic":
            from labeler.infra.llm.anthropic_adapter import AnthropicProvider

            return AnthropicProvider(api_key=key)
        if name == "slm":
            from labeler.infra.llm.slm_adapter import SLMProvider

            return SLMProvider(url)
        if name in ("openai", "openai_compat"):
            from labeler.infra.llm.openai_compat_adapter import OpenAICompatProvider

            return OpenAICompatProvider(url, key, name=name)
        raise LLMError(f"unknown provider {name!r}")

    async def test(self, name: str, model: str | None) -> dict[str, Any]:
        """List models if the provider can, then make one tiny structured call with `model`."""
        if name not in CATALOG:
            raise LLMError(f"unknown provider {name!r}")
        provider = self(name)
        out: dict[str, Any] = {"provider": name, "ok": False, "models": None, "model": model}
        lister = getattr(provider, "list_models", None)
        if lister is not None:
            try:
                out["models"] = await lister()
            except LLMError as e:
                out["error"] = str(e)
                return out
        if not model:
            out["ok"] = out["models"] is not None
            out["note"] = "connected; pick a model and test again to try a call"
            return out
        started = time.perf_counter()
        try:
            resp = await provider.complete(
                LLMRequest(model, "You are a connectivity check.", 'Reply with {"ok": true}.', PING_SCHEMA, "low", 200)
            )
        except LLMError as e:
            out["error"] = str(e)
            return out
        out.update(
            ok=bool(resp.data.get("ok")),
            latency_ms=round((time.perf_counter() - started) * 1000),
            reply=resp.data,
            usage={"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens},
        )
        return out

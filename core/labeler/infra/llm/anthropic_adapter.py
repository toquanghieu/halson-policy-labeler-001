"""Claude via the official Anthropic SDK, with JSON-schema structured output."""

from __future__ import annotations

import json
import time

import anthropic

from labeler.infra.llm.port import LLMError, LLMRequest, LLMResponse, Usage

# Models where a server-side refusal fallback is available and worth enabling.
_FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5"}
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, client: anthropic.AsyncAnthropic | None = None, api_key: str | None = None):
        # The key comes from the secret store (core/labeler/infra/secrets/); without one the SDK reads
        # ANTHROPIC_API_KEY (or an `ant auth login` profile) itself.
        self.client = client or anthropic.AsyncAnthropic(api_key=api_key, max_retries=3, timeout=60.0)

    async def list_models(self) -> list[str]:
        try:
            page = await self.client.models.list(limit=100)
        except anthropic.AuthenticationError as e:
            raise LLMError("authentication failed: check the Claude API key") from e
        except anthropic.APIError as e:
            raise LLMError(f"API error: {e}") from e
        return sorted(m.id for m in page.data)

    async def complete(self, req: LLMRequest) -> LLMResponse:
        params = dict(
            model=req.model,
            max_tokens=req.max_tokens,
            # The system block (instructions + policy) is identical for every
            # posting under one policy version, so it is cached across calls.
            system=[{"type": "text", "text": req.system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": req.user}],
            output_config={
                "effort": req.effort,
                "format": {"type": "json_schema", "schema": req.schema},
            },
        )
        started = time.perf_counter()
        try:
            if req.model in _FALLBACK_MODELS:
                msg = await self.client.beta.messages.create(**params, betas=[_FALLBACK_BETA], fallbacks="default")
            else:
                msg = await self.client.messages.create(**params)
        except anthropic.RateLimitError as e:
            raise LLMError(f"rate limited: {e.message}", retryable=True) from e
        except anthropic.BadRequestError as e:
            raise LLMError(f"bad request: {e.message}") from e
        except anthropic.AuthenticationError as e:
            raise LLMError("authentication failed: set ANTHROPIC_API_KEY") from e
        except anthropic.APIStatusError as e:
            raise LLMError(f"API error {e.status_code}: {e.message}", retryable=e.status_code >= 500) from e
        except anthropic.APIConnectionError as e:
            raise LLMError(f"connection error: {e}", retryable=True) from e
        except TypeError as e:  # the SDK found no key, token or profile: fails before any request
            if "authentication" not in str(e).lower():
                raise
            raise LLMError("no Claude API key: set ANTHROPIC_API_KEY or add it on the Settings page") from e
        latency_ms = (time.perf_counter() - started) * 1000

        u = msg.usage
        usage = Usage(
            input_tokens=u.input_tokens or 0,
            output_tokens=u.output_tokens or 0,
            cache_read_tokens=u.cache_read_input_tokens or 0,
            cache_write_tokens=u.cache_creation_input_tokens or 0,
        )
        if msg.stop_reason == "refusal":
            raise LLMError("model refused the request")
        if msg.stop_reason == "max_tokens":
            raise LLMError("output truncated at max_tokens", retryable=True)

        text = next((b.text for b in msg.content if b.type == "text"), "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise LLMError(f"model returned invalid JSON: {e}", retryable=True) from e
        return LLMResponse(data=data, model=msg.model, usage=usage, latency_ms=latency_ms, request_id=msg._request_id)

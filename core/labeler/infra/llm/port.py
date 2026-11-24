"""Provider-neutral interface the agent talks to."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

# USD per 1M tokens: (input, output, cache-read multiple of input). 5-minute cache writes bill at
# 1.25x input. Anthropic first-party list prices, 2026-11: cache reads are 0.05x on Opus 5.5 and
# Sonnet 5.5 ($0.20 and $0.10), 0.1x on Haiku 5.5. Bedrock has its own price list (DESIGN.md §3).
# A model not listed here (OpenAI, an OpenAI-compatible endpoint) is *unpriced*: its cost is
# recorded as 0 and flagged, never silently counted as free.
PRICES: dict[str, tuple[float, float, float]] = {
    "claude-haiku-5-5": (0.10, 0.50, 0.10),
    "claude-sonnet-5-5": (2.00, 10.00, 0.05),
    "claude-opus-5-5": (4.00, 20.00, 0.05),
    "fake": (0.0, 0.0, 0.0),
}
CACHE_WRITE_MULT = 1.25


def is_priced(model: str) -> bool:
    return model in PRICES


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def cost_usd(self, model: str) -> float:
        """List-price cost; 0.0 for an unpriced model (check `is_priced`)."""
        p_in, p_out, read_mult = PRICES.get(model, (0.0, 0.0, 0.0))
        return (
            self.input_tokens * p_in
            + self.cache_read_tokens * p_in * read_mult
            + self.cache_write_tokens * p_in * CACHE_WRITE_MULT
            + self.output_tokens * p_out
        ) / 1_000_000


@dataclass
class LLMRequest:
    model: str
    system: str
    user: str
    schema: dict[str, Any]
    effort: str = "low"
    max_tokens: int = 4000


@dataclass
class LLMResponse:
    data: dict[str, Any]
    model: str
    usage: Usage
    latency_ms: float
    request_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class LLMError(RuntimeError):
    """Raised for failures the agent should record rather than crash on."""

    def __init__(self, message: str, *, retryable: bool = False, raw: Any = None):
        super().__init__(message)
        self.retryable = retryable
        self.raw = raw  # what the model actually returned, when the failure is about its output


class LLMProvider(Protocol):
    name: str

    async def complete(self, req: LLMRequest) -> LLMResponse: ...

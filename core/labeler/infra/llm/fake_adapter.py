"""Scripted provider for tests. Never used to produce real labels."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from labeler.infra.llm.port import LLMRequest, LLMResponse, Usage

Handler = Callable[[LLMRequest], dict[str, Any]]


def _nothing_found(req: LLMRequest) -> dict[str, Any]:
    return {
        "findings": [],
        "insufficient_evidence": False,
        "rationale": "No policy section applies.",
        "confidence": 0.9,
    }


class FakeProvider:
    name = "fake"

    def __init__(self, handler: Handler | None = None):
        self.handler = handler or _nothing_found
        self.calls: list[LLMRequest] = []

    async def complete(self, req: LLMRequest) -> LLMResponse:
        self.calls.append(req)
        data = self.handler(req)
        return LLMResponse(
            data=data,
            model=req.model,
            usage=Usage(input_tokens=len(req.system + req.user) // 4, output_tokens=120),
            latency_ms=1.0,
        )

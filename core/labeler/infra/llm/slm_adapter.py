"""The distilled student (scripts/distill.py) as a provider.

Qwen2.5-1.5B-Instruct + LoRA adapter, served by `mlx_lm.server` on Apple silicon
(`make slm-serve`); in production the same contract is what vLLM or a SageMaker endpoint
exposes. It is an OpenAI-compatible endpoint without structured output, so the schema goes in
the system prompt: exactly the format the student was trained on.
"""

from __future__ import annotations

from labeler.infra.llm.openai_compat_adapter import OpenAICompatProvider, parse_json_reply, student_messages

__all__ = ["SLMProvider", "parse_json_reply", "student_messages"]


class SLMProvider(OpenAICompatProvider):
    def __init__(self, base_url: str, timeout_s: float = 120.0):
        super().__init__(
            base_url, None, name="slm", structured=False, send_effort=False, send_model=False, timeout_s=timeout_s
        )

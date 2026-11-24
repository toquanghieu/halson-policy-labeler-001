"""The distilled-student provider: request format and lenient JSON parsing."""

import asyncio

import httpx
import pytest

from labeler.infra.llm.port import LLMError, LLMRequest
from labeler.infra.llm.slm_adapter import SLMProvider, parse_json_reply


def test_parses_fenced_and_bare_json():
    assert parse_json_reply('```json\n{"findings": []}\n```') == {"findings": []}
    assert parse_json_reply('Here: {"a": 1} done') == {"a": 1}
    with pytest.raises(LLMError):
        parse_json_reply("no json at all")


def test_calls_chat_completions_with_schema_in_system_prompt():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.read().decode()
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"findings": [], "insufficient_evidence": false, "rationale": "r", "confidence": 0.8}'
                        }
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            },
        )

    p = SLMProvider("http://slm.test/v1")
    p.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    r = asyncio.run(p.complete(LLMRequest("slm", "SYSTEM", "USER", {"type": "object"})))
    assert r.data["confidence"] == 0.8 and r.usage.output_tokens == 20
    assert '"model"' not in seen["body"]  # mlx_lm.server would try to download it
    assert "JSON schema" in seen["body"] and '"temperature":0' in seen["body"]


def test_server_down_is_retryable_error():
    def handler(request):
        raise httpx.ConnectError("refused")

    p = SLMProvider("http://slm.test/v1")
    p.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(LLMError) as e:
        asyncio.run(p.complete(LLMRequest("slm", "s", "u", {})))
    assert e.value.retryable

"""The two real providers, with the SDK client and the CLI subprocess replaced by stubs.

These pin down the request we send (caching, schema, fallback beta, CLI flags) and how
each failure maps to a retryable or final LLMError. No network, no key, no CLI needed.
"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from labeler.infra.llm import claude_cli_adapter as claude_cli
from labeler.infra.llm.anthropic_adapter import AnthropicProvider
from labeler.infra.llm.claude_cli_adapter import ClaudeCLIProvider
from labeler.infra.llm.port import LLMError, LLMRequest

SCHEMA = {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]}


def request(model: str = "claude-haiku-5-5") -> LLMRequest:
    return LLMRequest(model=model, system="SYSTEM", user="USER", schema=SCHEMA, effort="low")


def run(coro):
    return asyncio.run(coro)


# ---- Anthropic SDK ------------------------------------------------------------
def message(text: str = '{"x": 1}', stop_reason: str = "end_turn", model: str = "claude-haiku-5-5"):
    msg = SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
        model=model,
        usage=SimpleNamespace(
            input_tokens=10, output_tokens=5, cache_read_input_tokens=900, cache_creation_input_tokens=0
        ),
    )
    msg._request_id = "req_1"
    return msg


class StubClient:
    def __init__(self, reply=None, error: Exception | None = None):
        self.calls: list[tuple[str, dict]] = []
        self._reply, self._error = reply or message(), error

        async def create(kind, **kw):
            self.calls.append((kind, kw))
            if self._error:
                raise self._error
            return self._reply

        self.messages = SimpleNamespace(create=lambda **kw: create("plain", **kw))
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: create("beta", **kw)))


def api_error(cls, status: int):
    response = httpx.Response(status, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))
    return cls("boom", response=response, body=None)


def test_anthropic_request_caches_policy_and_uses_json_schema():
    client = StubClient()
    resp = run(AnthropicProvider(client).complete(request()))
    kind, kw = client.calls[0]
    assert kind == "plain"  # no fallback beta for Haiku
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert kw["output_config"] == {"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}}
    assert resp.data == {"x": 1} and resp.usage.cache_read_tokens == 900 and resp.request_id == "req_1"


def test_anthropic_opus_goes_through_refusal_fallback_beta():
    client = StubClient(message(model="claude-opus-5-5"))
    run(AnthropicProvider(client).complete(request("claude-opus-5-5")))
    kind, kw = client.calls[0]
    assert kind == "beta" and kw["fallbacks"] == "default" and kw["betas"] == ["server-side-fallback-2026-07-01"]


@pytest.mark.parametrize(
    ("reply", "error", "retryable"),
    [
        (message(stop_reason="refusal"), None, False),
        (message(stop_reason="max_tokens"), None, True),
        (message(text="not json"), None, True),
        (None, api_error(anthropic.RateLimitError, 429), True),
        (None, api_error(anthropic.InternalServerError, 500), True),
        (None, api_error(anthropic.BadRequestError, 400), False),
        (None, api_error(anthropic.AuthenticationError, 401), False),
    ],
    ids=["refusal", "truncated", "bad-json", "429", "500", "400", "401"],
)
def test_anthropic_failures_map_to_llm_error(reply, error, retryable):
    with pytest.raises(LLMError) as exc:
        run(AnthropicProvider(StubClient(reply, error)).complete(request()))
    assert exc.value.retryable is retryable


# ---- Claude CLI -------------------------------------------------------------------
class StubProc:
    def __init__(self, stdout: bytes, returncode: int = 0):
        self.stdout, self.returncode = stdout, returncode
        self.stdin_seen: bytes | None = None
        self.killed = False

    async def communicate(self, data: bytes):
        self.stdin_seen = data
        return self.stdout, b"stderr text"

    def kill(self):
        self.killed = True


def cli_output(**over) -> bytes:
    out = {
        "subtype": "success",
        "is_error": False,
        "structured_output": {"x": 1},
        "usage": {
            "input_tokens": 3,
            "output_tokens": 7,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 50,
        },
        "modelUsage": {"claude-haiku-5-5": {}},
        "session_id": "s1",
        "total_cost_usd": 0.001,
    }
    out.update(over)
    return json.dumps(out).encode()


@pytest.fixture
def fake_exec(monkeypatch):
    """Capture the CLI invocation; tests set `.proc` to control what it returns."""
    seen = SimpleNamespace(args=None, proc=StubProc(cli_output()), system_file=None)

    async def create_subprocess_exec(*args, **_kw):
        seen.args = list(args)
        seen.system_file = Path(args[args.index("--system-prompt-file") + 1])
        assert seen.system_file.read_text() == "SYSTEM"
        return seen.proc

    monkeypatch.setattr(claude_cli.asyncio, "create_subprocess_exec", create_subprocess_exec)
    return seen


def test_cli_invocation_is_isolated_and_parses_structured_output(fake_exec):
    resp = run(ClaudeCLIProvider().complete(request()))
    a = fake_exec.args
    assert a[a.index("--model") + 1] == "haiku" and a[a.index("--effort") + 1] == "low"
    assert a[a.index("--tools") + 1] == "" and "--strict-mcp-config" in a and "--no-session-persistence" in a
    assert json.loads(a[a.index("--json-schema") + 1]) == SCHEMA
    assert fake_exec.proc.stdin_seen == b"USER"  # posting goes over stdin, not argv
    assert not fake_exec.system_file.exists()  # temp file cleaned up
    assert resp.data == {"x": 1} and resp.usage.cache_write_tokens == 50 and resp.model == "claude-haiku-5-5"


@pytest.mark.parametrize(
    "stdout",
    [b"not json", cli_output(is_error=True, subtype="error_during_execution"), cli_output(structured_output=None)],
    ids=["non-json", "cli-error", "no-structured-output"],
)
def test_cli_failures_are_retryable_llm_errors(fake_exec, stdout):
    fake_exec.proc = StubProc(stdout, returncode=1)
    with pytest.raises(LLMError) as exc:
        run(ClaudeCLIProvider().complete(request()))
    assert exc.value.retryable


def test_cli_missing_binary_is_final(monkeypatch):
    async def missing(*_a, **_kw):
        raise FileNotFoundError

    monkeypatch.setattr(claude_cli.asyncio, "create_subprocess_exec", missing)
    with pytest.raises(LLMError) as exc:
        run(ClaudeCLIProvider().complete(request()))
    assert not exc.value.retryable


def test_prices_cache_reads_per_model_and_flag_unpriced_models():
    from labeler.infra.llm.port import Usage, is_priced

    u = Usage(input_tokens=1_000_000, cache_read_tokens=1_000_000)
    assert u.cost_usd("claude-opus-5-5") == 4.00 + 0.20  # cache reads 0.05x on Opus 5.5
    assert u.cost_usd("claude-sonnet-5-5") == 2.00 + 0.10
    assert round(u.cost_usd("claude-haiku-5-5"), 6) == 0.10 + 0.01  # 0.1x on Haiku 5.5
    assert not is_priced("gpt-5.5-mini") and u.cost_usd("gpt-5.5-mini") == 0.0


def test_unpriced_model_is_flagged_in_the_trace_and_the_metrics(settings, policy_v1, plain_posting):
    import asyncio

    from labeler.infra.llm.port import LLMResponse, Usage
    from labeler.labeling.agent import LabelingAgent
    from labeler.labeling.metrics import evaluate

    class Other:
        name = "openai"

        async def complete(self, req):
            data = {"findings": [], "insufficient_evidence": False, "rationale": "ok", "confidence": 0.9}
            return LLMResponse(data=data, model="gpt-5.5-mini", usage=Usage(input_tokens=1000), latency_ms=1.0)

    s = settings.__class__(**{**settings.__dict__, "tier2_model": ""})
    r = asyncio.run(LabelingAgent(Other(), s).label(plain_posting, policy_v1))
    assert r.trace["cost_unpriced"] == ["gpt-5.5-mini"] and r.trace["cost_usd"] == 0.0
    m = evaluate([{"label": "ALLOWED", "gold": 0, "cost_usd": 0.0, "cost_unpriced": True, "tier": "tier1"}])
    assert m["cost_unpriced"] == 1

"""Any model behind an OpenAI-style Chat Completions endpoint.

One adapter, many services:
    OpenAI                         https://api.openai.com/v1
    Google Gemini (free tier)      https://generativelanguage.googleapis.com/v1beta/openai
    Groq (free tier)               https://api.groq.com/openai/v1
    OpenRouter (":free" models)    https://openrouter.ai/api/v1
    Ollama, local, free            http://localhost:11434/v1  (from a container: host.docker.internal)
    the distilled student          http://localhost:8090/v1   (mlx_lm.server; core/labeler/infra/llm/slm_adapter.py)

Structured output: it asks for a strict `json_schema` response format. Servers that reject it
get the schema in the system prompt instead (the format the student was trained on), and the
reply is parsed leniently. Optional parameters a model rejects (`reasoning_effort`,
`temperature`) are dropped and remembered per model. Whatever comes back still goes through
the agent's quote verification and the policy's aggregation, as with Claude.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx

from labeler.infra.llm.port import LLMError, LLMRequest, LLMResponse, Usage

_EFFORT = {"low": "low", "medium": "medium", "high": "high", "xhigh": "high", "max": "high"}


def student_messages(system: str, user: str, schema: dict[str, Any]) -> list[dict[str, str]]:
    """Schema stated in the prompt: for servers without structured output (and the trained student)."""
    fmt = "\n\n# Output\nRespond with one JSON object and nothing else, matching this JSON schema:\n" + json.dumps(
        schema, ensure_ascii=False
    )
    return [{"role": "system", "content": system + fmt}, {"role": "user", "content": user}]


def parse_json_reply(text: str) -> dict[str, Any]:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise LLMError("reply has no JSON object")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as e:
        raise LLMError(f"reply is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise LLMError("reply is not a JSON object")
    return data


class OpenAICompatProvider:
    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        *,
        name: str = "openai_compat",
        structured: bool = True,
        send_effort: bool = True,
        send_model: bool = True,
        timeout_s: float = 120.0,
    ):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.structured = structured
        self.send_effort = send_effort
        # mlx_lm.server treats `model` as a HuggingFace repo to download; a server that serves
        # exactly one model (the student) must not receive it.
        self.send_model = send_model
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.client = httpx.AsyncClient(timeout=timeout_s, headers=headers)
        self._dropped: dict[str, set[str]] = {}  # model -> optional params it rejected
        self._no_schema: set[str] = set()  # models whose server rejected json_schema

    def _body(self, req: LLMRequest) -> tuple[dict[str, Any], set[str]]:
        dropped = self._dropped.setdefault(req.model, set())
        structured = self.structured and req.model not in self._no_schema
        msgs = (
            [{"role": "system", "content": req.system}, {"role": "user", "content": req.user}]
            if structured
            else student_messages(req.system, req.user, req.schema)
        )
        body: dict[str, Any] = {"messages": msgs, "max_completion_tokens": req.max_tokens}
        if self.send_model:
            body["model"] = req.model
        optional: set[str] = set()
        if structured:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "policy_findings", "schema": req.schema, "strict": True},
            }
        if self.send_effort and "reasoning_effort" not in dropped:
            body["reasoning_effort"] = _EFFORT.get(req.effort, "low")
            optional.add("reasoning_effort")
        if "temperature" not in dropped:
            body["temperature"] = 0
            optional.add("temperature")
        if "max_completion_tokens" in dropped:
            body["max_tokens"] = body.pop("max_completion_tokens")
        return body, optional | {"max_completion_tokens"}

    async def _post(self, body: dict[str, Any]) -> httpx.Response:
        try:
            return await self.client.post(f"{self.base_url}/chat/completions", json=body)
        except httpx.HTTPError as e:
            raise LLMError(f"{self.name} unreachable at {self.base_url}: {e}", retryable=True) from e

    async def complete(self, req: LLMRequest) -> LLMResponse:
        started = time.perf_counter()
        for _ in range(4):  # at most: drop effort, drop temperature, drop schema
            body, optional = self._body(req)
            r = await self._post(body)
            if r.status_code == 400:
                msg = r.text.lower()
                hit = next((p for p in optional if p in msg and p in body), None)
                if hit:
                    self._dropped[req.model].add(hit)
                    continue
                if "response_format" in body and ("response_format" in msg or "json_schema" in msg):
                    self._no_schema.add(req.model)
                    continue
            break
        if r.status_code in (401, 403):
            raise LLMError(f"{self.name}: authentication failed ({r.status_code}); check the API key")
        if r.status_code == 429:
            raise LLMError(f"{self.name}: rate limited", retryable=True)
        if r.status_code >= 500:
            raise LLMError(f"{self.name}: server error {r.status_code}", retryable=True)
        if r.status_code >= 400:
            raise LLMError(f"{self.name}: rejected ({r.status_code}): {r.text[:300]}")
        payload = r.json()
        choice = payload["choices"][0]
        if choice.get("finish_reason") == "length":
            raise LLMError("output truncated at max tokens", retryable=True)
        text = choice["message"].get("content") or ""
        if choice["message"].get("refusal"):
            raise LLMError(f"model refused: {choice['message']['refusal']}")
        usage = payload.get("usage") or {}
        return LLMResponse(
            data=parse_json_reply(text),
            model=payload.get("model") or req.model,
            usage=Usage(input_tokens=usage.get("prompt_tokens", 0), output_tokens=usage.get("completion_tokens", 0)),
            latency_ms=(time.perf_counter() - started) * 1000,
            request_id=r.headers.get("x-request-id"),
            extra={"raw_text": text},
        )

    async def list_models(self) -> list[str]:
        try:
            r = await self.client.get(f"{self.base_url}/models")
        except httpx.HTTPError as e:
            raise LLMError(f"{self.name} unreachable at {self.base_url}: {e}") from e
        if r.status_code in (401, 403):
            raise LLMError(f"{self.name}: authentication failed ({r.status_code}); check the API key")
        if r.status_code >= 400:
            raise LLMError(f"{self.name}: /models returned {r.status_code}")
        return sorted(m["id"] for m in r.json().get("data", []) if "id" in m)

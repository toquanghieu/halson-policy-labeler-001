"""Claude through the local `claude` CLI (Claude Code, print mode).

Development convenience for running the full agent without an API key: it uses
whatever login the CLI has. Same prompt, same schema, same verification. Not the
production path: each call starts a process (~1 s overhead) and the CLI adds its
own wrapper tokens, so latency and token counts are higher than the API's.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time

from labeler.infra.llm.port import LLMError, LLMRequest, LLMResponse, Usage

# CLI aliases for the model ids used in settings.
_ALIASES = {"claude-haiku-5-5": "haiku", "claude-sonnet-5-5": "sonnet", "claude-opus-5-5": "opus"}


class ClaudeCLIProvider:
    name = "claude_cli"

    def __init__(self, binary: str = "claude", timeout_s: float = 180.0):
        self.binary = binary
        self.timeout_s = timeout_s

    async def complete(self, req: LLMRequest) -> LLMResponse:
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write(req.system)
            system_file = f.name
        args = [
            self.binary,
            "-p",
            "--model",
            _ALIASES.get(req.model, req.model),
            "--effort",
            req.effort,
            "--tools",
            "",
            "--strict-mcp-config",
            "--setting-sources",
            "",
            "--no-session-persistence",
            "--system-prompt-file",
            system_file,
            "--json-schema",
            json.dumps(req.schema),
            "--output-format",
            "json",
        ]
        started = time.perf_counter()
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, err = await asyncio.wait_for(proc.communicate(req.user.encode()), self.timeout_s)
        except TimeoutError as e:
            proc.kill()
            raise LLMError("claude CLI timed out", retryable=True) from e
        except FileNotFoundError as e:
            raise LLMError("claude CLI not found on PATH") from e
        finally:
            os.unlink(system_file)
        latency_ms = (time.perf_counter() - started) * 1000

        try:
            d = json.loads(out)
        except json.JSONDecodeError as e:
            raise LLMError(
                f"claude CLI returned non-JSON (exit {proc.returncode}): {err.decode()[:300]}", retryable=True
            ) from e
        if d.get("is_error") or d.get("subtype") != "success":
            raise LLMError(
                f"claude CLI error: {d.get('api_error_status') or d.get('result', '')[:300]}", retryable=True
            )
        data = d.get("structured_output")
        if not isinstance(data, dict):
            raise LLMError("claude CLI returned no structured output", retryable=True)

        u = d.get("usage", {})
        usage = Usage(
            input_tokens=u.get("input_tokens", 0),
            output_tokens=u.get("output_tokens", 0),
            cache_read_tokens=u.get("cache_read_input_tokens", 0),
            cache_write_tokens=u.get("cache_creation_input_tokens", 0),
        )
        model = next(iter(d.get("modelUsage", {})), req.model)
        return LLMResponse(
            data=data,
            model=model,
            usage=usage,
            latency_ms=latency_ms,
            request_id=d.get("session_id"),
            extra={"cli_reported_cost_usd": d.get("total_cost_usd")},
        )

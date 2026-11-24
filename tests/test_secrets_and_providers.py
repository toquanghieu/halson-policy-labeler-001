"""API keys (encrypted, write-only), OpenAI-compatible providers, per-tier provider choice."""

import asyncio
import json
import os
import stat

import httpx
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws

from labeler.errors import InvalidInput
from labeler.infra.db import DB, make_engine
from labeler.infra.db.migrate import upgrade
from labeler.infra.llm.fake_adapter import FakeProvider
from labeler.infra.llm.openai_compat_adapter import OpenAICompatProvider
from labeler.infra.llm.port import LLMError, LLMRequest
from labeler.infra.secrets.aws_adapter import AwsSecrets
from labeler.infra.secrets.db_adapter import EncryptedDbSecrets
from labeler.infra.secrets.env_adapter import EnvSecrets
from labeler.infra.storage.local_adapter import LocalStore
from labeler.labeling.agent import LabelingAgent
from labeler_api.main import create_app

SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}


def _db(tmp_path) -> DB:
    engine = make_engine(f"sqlite:///{tmp_path / 's.db'}", pool_size=2)
    upgrade(engine)
    return DB(engine, LocalStore(tmp_path / "objects"))


def test_db_secrets_are_encrypted_write_only_and_fall_back_to_env(tmp_path, monkeypatch):
    db = _db(tmp_path)
    store = EncryptedDbSecrets(db, key_file=tmp_path / "secrets.key")
    assert stat.S_IMODE(os.stat(tmp_path / "secrets.key").st_mode) == 0o600

    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env-1234")
    assert store.get("OPENAI_API_KEY") == "sk-from-env-1234"
    assert store.describe("OPENAI_API_KEY") == {
        "name": "OPENAI_API_KEY",
        "set": True,
        "source": "environment",
        "hint": "…1234",
    }

    store.set("OPENAI_API_KEY", "sk-typed-in-settings-9876")
    row = db.get_secret_row("OPENAI_API_KEY")
    assert "sk-typed" not in row["ciphertext"] and row["hint"] == "…9876"
    assert store.get("OPENAI_API_KEY") == "sk-typed-in-settings-9876"
    # Another process (a worker) with the same master key reads it.
    assert (
        EncryptedDbSecrets(db, key_file=tmp_path / "secrets.key").get("OPENAI_API_KEY") == "sk-typed-in-settings-9876"
    )

    with pytest.raises(InvalidInput):
        store.set("DATABASE_URL", "x")  # only known secrets
    assert store.delete("OPENAI_API_KEY")
    assert store.get("OPENAI_API_KEY") == "sk-from-env-1234"
    with pytest.raises(InvalidInput):
        EnvSecrets().set("OPENAI_API_KEY", "x")
    db.close()


@mock_aws
def test_aws_secrets_manager_store():
    store = AwsSecrets("policy-labeler/", "us-east-1")
    assert store.get("ANTHROPIC_API_KEY") is None or os.environ.get("ANTHROPIC_API_KEY")
    store.set("ANTHROPIC_API_KEY", "sk-ant-secret-abcd")
    store._cache.clear()
    assert store.get("ANTHROPIC_API_KEY") == "sk-ant-secret-abcd"
    assert store.describe("ANTHROPIC_API_KEY")["source"] == "AWS Secrets Manager"
    store.set("ANTHROPIC_API_KEY", "sk-ant-rotated-wxyz")  # put_secret_value on an existing secret
    assert store.get("ANTHROPIC_API_KEY") == "sk-ant-rotated-wxyz"


def _provider(handler, **kw) -> OpenAICompatProvider:
    p = OpenAICompatProvider("http://llm.test/v1", "sk-test", **kw)
    p.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), headers={"Authorization": "Bearer sk-test"})
    return p


def _ok(content='{"ok": true}'):
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 9, "completion_tokens": 3},
        },
    )


def test_openai_compat_uses_strict_schema_and_adapts_to_the_server():
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        bodies.append(body)
        assert request.headers["authorization"] == "Bearer sk-test"
        if "reasoning_effort" in body:
            return httpx.Response(400, json={"error": {"message": "Unrecognized request argument: reasoning_effort"}})
        if "response_format" in body:
            return httpx.Response(400, json={"error": {"message": "response_format json_schema is not supported"}})
        return _ok()

    p = _provider(handler)
    r = asyncio.run(p.complete(LLMRequest("some-model", "sys", "user", SCHEMA)))
    assert r.data == {"ok": True} and r.usage.input_tokens == 9
    assert bodies[0]["response_format"]["json_schema"]["strict"] is True
    assert "reasoning_effort" not in bodies[-1] and "response_format" not in bodies[-1]
    assert "JSON schema" in bodies[-1]["messages"][0]["content"]  # fell back to the schema in the prompt
    n = len(bodies)
    asyncio.run(p.complete(LLMRequest("some-model", "sys", "user", SCHEMA)))
    assert len(bodies) == n + 1  # remembered: no more rejected attempts


def test_openai_compat_errors_and_model_list():
    def denied(request):
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    with pytest.raises(LLMError, match="authentication failed"):
        asyncio.run(_provider(denied).complete(LLMRequest("m", "s", "u", SCHEMA)))

    def models(request):
        return httpx.Response(200, json={"data": [{"id": "gpt-b"}, {"id": "gpt-a"}]})

    assert asyncio.run(_provider(models).list_models()) == ["gpt-a", "gpt-b"]


def test_each_tier_can_use_a_different_provider(settings, plain_posting, policy_v1):
    cheap = FakeProvider(
        lambda r: {"findings": [], "insufficient_evidence": False, "rationale": "r", "confidence": 0.3}
    )
    strong = FakeProvider(
        lambda r: {"findings": [], "insufficient_evidence": False, "rationale": "r", "confidence": 0.9}
    )
    s = settings.model_copy(update={"tier1_provider": "openai_compat", "tier2_provider": "anthropic"})
    agent = LabelingAgent({"openai_compat": cheap, "anthropic": strong}.__getitem__, s)
    r = asyncio.run(agent.label(plain_posting, policy_v1))
    assert [st["provider"] for st in r.trace["steps"]] == ["openai_compat", "anthropic"]
    assert len(cheap.calls) == 1 and len(strong.calls) == 1


def test_settings_api_providers_secrets_and_test(settings):
    ping = FakeProvider(lambda r: {"ok": True})
    with TestClient(create_app(settings, ping)) as c:
        prov = c.get("/api/settings/providers").json()
        assert prov["secrets_backend"] == "db"
        names = [p["name"] for p in prov["providers"]]
        assert {"anthropic", "openai", "openai_compat", "slm", "claude_cli"} <= set(names)

        r = c.put("/api/settings/secrets/OPENAI_API_KEY", json={"value": "sk-live-abcdefgh1234"})
        assert r.json()["hint"] == "…1234" and "sk-live" not in r.text
        listed = c.get("/api/settings/providers").text
        assert "sk-live" not in listed and "…1234" in listed
        assert c.put("/api/settings/secrets/NOPE", json={"value": "x"}).status_code == 404

        t = c.post("/api/settings/providers/openai/test", json={"model": "gpt-x"}).json()
        assert t["ok"] is True and t["reply"] == {"ok": True}
        assert c.post("/api/settings/providers/nope/test", json={}).status_code == 404

        up = c.put("/api/settings", json={"values": {"tier1_provider": "openai", "tier1_model": "gpt-x"}})
        assert up.status_code == 200 and c.get("/api/health").json()["tier1_provider"] == "openai"
        assert c.put("/api/settings", json={"values": {"tier1_provider": "nope"}}).status_code == 422
        assert c.delete("/api/settings/secrets/OPENAI_API_KEY").json()["set"] in (True, False)

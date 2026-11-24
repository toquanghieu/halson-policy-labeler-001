"""Worker heartbeats, and what the case view shows when a label is queued but nobody consumes."""

import time

from fastapi.testclient import TestClient

from labeler.config import REPO_ROOT
from labeler.infra.llm.fake_adapter import FakeProvider
from labeler.labeling.schemas import Posting
from labeler_api.main import create_app
from tests.test_api import policy_aware_handler

CLERICAL = {"job_id": "c1", "title": "Clerk", "description": "Easy home-based data entry, start today."}


def test_embedded_worker_reports_itself(settings):
    with TestClient(create_app(settings, FakeProvider())) as c:
        deadline = time.time() + 3
        while time.time() < deadline and not c.get("/api/health").json()["workers"]:
            time.sleep(0.05)
        (w,) = c.get("/api/health").json()["workers"]
        assert w["role"] == "embedded" and w["concurrency"] == settings.max_concurrency
    # Clean shutdown removes the row.
    with TestClient(create_app(settings.model_copy(update={"embedded_worker": False}), FakeProvider())) as c:
        assert c.get("/api/health").json()["workers"] == []


def test_queued_label_survives_a_refresh_when_no_worker_runs(settings):
    no_worker = settings.model_copy(update={"embedded_worker": False})
    with TestClient(create_app(no_worker, FakeProvider(policy_aware_handler))) as c:
        c.app.state.service.db.upsert_postings([(Posting(**CLERICAL), None, False)])
        c.post("/api/policies", json={"source": (REPO_ROOT / "policies" / "v2.yaml").read_text()})
        job = c.post("/api/policies/v2/relabel", json={"scope": "ids", "ids": ["c1"]}).json()["job_id"]
        # A new page load asks again: the version is still unlabeled, and the pending job is reported.
        view = c.get("/api/postings/c1/versions?v=v1&v=v2").json()["versions"]
        assert (
            view[1]["result"] is None and view[1]["state"]["status"] == "queued" and view[1]["state"]["run_id"] == job
        )
        assert view[0]["state"] is None or view[0]["state"]["status"] == "done"
        assert c.get("/api/health").json()["workers"] == []


def test_status_moves_queued_labeling_done_and_failed(settings):
    from labeler.infra.llm.port import LLMError

    def flaky(req):
        if "boom" in req.user:
            raise LLMError("model unavailable")
        return policy_aware_handler(req)

    no_worker = settings.model_copy(update={"embedded_worker": False})
    with TestClient(create_app(no_worker, FakeProvider(flaky))) as c:
        svc = c.app.state.service
        svc.db.upsert_postings([(Posting(**CLERICAL), None, False), (Posting(job_id="b1", title="boom"), None, False)])
        job = c.post("/api/inbox/label", json={"ids": ["c1", "b1"]}).json()["job_id"]
        assert {i["job_id"]: i["state"]["status"] for i in c.get("/api/inbox?scope=imported").json()["items"]} == {
            "c1": "queued",
            "b1": "queued",
        }
        svc.db.mark_labeling("c1", "v1", job)
        assert svc.db.statuses_of("c1")["v1"]["status"] == "labeling"

        import asyncio

        asyncio.run(svc.process_item(job, "c1", "v1"))
        asyncio.run(svc.process_item(job, "b1", "v1"))
        assert svc.db.statuses_of("c1")["v1"]["status"] == "done"
        failed = svc.db.statuses_of("b1")["v1"]
        assert failed["status"] == "failed" and "model unavailable" in failed["error"]
        assert c.get("/api/policies/v1").json()["label_status"] == {"done": 1, "failed": 1}

        # A dead-lettered message (no label written) is failed too, with the reason.
        job2 = c.post("/api/inbox/label", json={"ids": ["c1"]}).json()["job_id"]
        svc.db.mark_item(job2, "c1", failed=True, error="gave up after 3 attempts")
        assert svc.db.statuses_of("c1")["v1"] | {"updated_at": None} == {
            "status": "failed",
            "run_id": job2,
            "error": "gave up after 3 attempts",
            "updated_at": None,
        }


def test_cancel_job_skips_its_messages(settings):
    import asyncio

    no_worker = settings.model_copy(update={"embedded_worker": False})
    provider = FakeProvider(policy_aware_handler)
    with TestClient(create_app(no_worker, provider)) as c:
        svc = c.app.state.service
        svc.db.upsert_postings([(Posting(**CLERICAL), None, False), (Posting(job_id="d2", title="t"), None, False)])
        asyncio.run(svc.run_ids(svc.policy(), ["c1"]))  # c1 already labeled under v1
        job = c.post("/api/inbox/label", json={"ids": ["c1", "d2"]}).json()["job_id"]
        r = c.post(f"/api/jobs/{job}/cancel").json()
        assert r == {"job_id": job, "status": "cancelled", "cancelled_items": 2}
        assert c.get(f"/api/jobs/{job}").json()["status"] == "cancelled"
        assert svc.db.statuses_of("c1")["v1"]["status"] == "done"  # back to what its label says
        assert "v1" not in svc.db.statuses_of("d2")
        provider.calls.clear()
        assert asyncio.run(svc.process_item(job, "d2", "v1")) is False and provider.calls == []  # skipped
        assert c.post(f"/api/jobs/{job}/cancel").status_code == 409
        assert c.post("/api/jobs/999/cancel").status_code == 404


def test_requeue_resends_only_pending_items(settings):
    import asyncio

    no_worker = settings.model_copy(update={"embedded_worker": False})
    with TestClient(create_app(no_worker, FakeProvider(policy_aware_handler))) as c:
        svc = c.app.state.service
        svc.db.upsert_postings([(Posting(**CLERICAL), None, False), (Posting(job_id="d2", title="t"), None, False)])
        job = c.post("/api/inbox/label", json={"ids": ["c1", "d2"]}).json()["job_id"]
        asyncio.run(svc.process_item(job, "c1", "v1"))
        svc.queue._q["live"].clear()  # the queue lost its messages (local restart)
        assert c.post(f"/api/jobs/{job}/requeue").json() == {"job_id": job, "resent": 1}
        assert [m.body["posting_id"] for m in asyncio.run(svc.queue.receive("live", 10, 0))] == ["d2"]
        assert c.post("/api/jobs/999/requeue").status_code == 404


def test_registry_says_why_a_provider_cannot_be_called(settings, tmp_path, monkeypatch):
    from labeler.infra.llm.registry import ProviderRegistry
    from labeler.infra.secrets.env_adapter import EnvSecrets

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    reg = ProviderRegistry(lambda: settings, EnvSecrets())
    assert "ANTHROPIC_API_KEY is not set" in reg.unavailable("anthropic")
    assert "OPENAI_API_KEY is not set" in reg.unavailable("openai")
    assert reg.unavailable("openai_compat") is None  # a local Ollama needs no key
    assert reg.unavailable("fake") is None and reg.unavailable("slm") is None
    monkeypatch.setattr("labeler.infra.llm.registry.shutil.which", lambda _: None)
    assert "host only" in reg.unavailable("claude_cli")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert reg.unavailable("anthropic") is None


def test_worker_waits_without_a_key_and_starts_when_one_is_added(settings):
    """A missing key must leave postings queued (not failed) and need no restart once fixed."""
    with TestClient(create_app(settings, FakeProvider(policy_aware_handler))) as c:
        svc = c.app.state.service
        c.app.state.worker.ready_check_s = 0.05
        blocked = {"reason": "tier 1 (anthropic): ANTHROPIC_API_KEY is not set (add it on the Settings page)"}
        svc.blocked_reason = lambda: blocked["reason"]

        def worker_row():
            deadline = time.time() + 5
            while time.time() < deadline:
                ws = c.get("/api/health").json()["workers"]
                if ws and ws[0]["status"] == ("waiting" if blocked["reason"] else "running"):
                    return ws[0]
                time.sleep(0.05)
            raise AssertionError(c.get("/api/health").json()["workers"])

        w = worker_row()
        assert w["status"] == "waiting" and "ANTHROPIC_API_KEY" in w["detail"]

        svc.db.upsert_postings([(Posting(**CLERICAL), None, False)])
        c.post("/api/inbox/label", json={"ids": ["c1"]})
        time.sleep(0.5)
        assert svc.db.statuses_of("c1")["v1"]["status"] == "queued"  # untouched, not failed

        blocked["reason"] = None  # the key was added on the Settings page
        assert worker_row()["detail"] == ""
        deadline = time.time() + 10
        while time.time() < deadline and svc.db.statuses_of("c1")["v1"]["status"] != "done":
            time.sleep(0.05)
        assert svc.db.statuses_of("c1")["v1"]["status"] == "done"


def test_work_view_lists_queued_labeling_and_failed(settings):
    no_worker = settings.model_copy(update={"embedded_worker": False})
    with TestClient(create_app(no_worker, FakeProvider(policy_aware_handler))) as c:
        svc = c.app.state.service
        svc.db.upsert_postings([(Posting(**CLERICAL), None, False), (Posting(job_id="b1", title="Boom"), None, False)])
        job = c.post("/api/inbox/label", json={"ids": ["c1", "b1"]}).json()["job_id"]
        svc.db.mark_labeling("b1", "v1", job)

        w = c.get("/api/work").json()
        assert w["counts"] == {"v1": {"queued": 1, "labeling": 1}} and w["total"] == 2
        assert [(i["posting_id"], i["status"]) for i in w["items"]] == [("b1", "labeling"), ("c1", "queued")]
        assert w["items"][1]["title"] == "Clerk" and w["items"][1]["run_id"] == job
        assert [j["id"] for j in w["jobs"]] == [job] and w["workers"] == []
        assert w["queues"]["live"]["visible"] == 2  # the broker's view next to the database's

        only = c.get("/api/work?status=queued&version=v1").json()
        assert [i["posting_id"] for i in only["items"]] == ["c1"] and only["total"] == 1
        assert c.get("/api/work?version=v2").json()["items"] == []


def test_sync_label_without_a_key_is_503_with_the_reason_not_500(settings):
    no_worker = settings.model_copy(update={"embedded_worker": False})
    with TestClient(create_app(no_worker, FakeProvider(policy_aware_handler))) as c:
        svc = c.app.state.service
        svc.blocked_reason = lambda: "tier 1 (anthropic): ANTHROPIC_API_KEY is not set (add it on the Settings page)"
        r = c.post("/api/postings", json=CLERICAL)
        assert r.status_code == 503 and "ANTHROPIC_API_KEY is not set" in r.json()["detail"]
        assert c.get("/api/results/c1").status_code == 404  # nothing stored


def test_anthropic_provider_without_any_key_raises_llm_error(monkeypatch):
    import asyncio

    import pytest

    from labeler.infra.llm.anthropic_adapter import AnthropicProvider
    from labeler.infra.llm.port import LLMError, LLMRequest

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("HOME", "/nonexistent")  # no `ant auth login` profile either
    p = AnthropicProvider(api_key=None)
    req = LLMRequest(model="claude-haiku-5-5", system="s", user="u", schema={"type": "object"})
    with pytest.raises(LLMError, match="no Claude API key"):
        asyncio.run(p.complete(req))

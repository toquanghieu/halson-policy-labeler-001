"""Run-time settings, publish + backfill, rollback, and the per-posting multi-version view."""

from fastapi.testclient import TestClient

from labeler.config import REPO_ROOT
from labeler.infra.llm.fake_adapter import FakeProvider
from labeler.services.settings import CACHE_S
from labeler_api.main import create_app
from tests.test_api import policy_aware_handler, wait_job

CLERICAL = {"job_id": "c1", "title": "Clerk", "description": "Easy home-based data entry, start today."}
MULE = {"job_id": "m1", "title": "Agent", "description": "Receive checks and forward them via Western Union."}


def test_settings_change_models_and_thresholds_without_restart(settings):
    provider = FakeProvider(
        lambda req: {"findings": [], "insufficient_evidence": False, "rationale": "r", "confidence": 0.5}
    )
    with TestClient(create_app(settings, provider)) as c:
        s = c.get("/api/settings").json()
        assert s["values"]["tier1_model"] == "fake-small" and s["overridden"] == []
        assert "claude-haiku-5-5" in s["choices"]["models"]

        c.post("/api/postings", json={"job_id": "a", "title": "t"})
        assert [r.model for r in provider.calls] == ["fake-small", "fake-large"]  # 0.5 < 0.6: escalated

        r = c.put(
            "/api/settings", json={"values": {"tier1_model": "claude-sonnet-5-5", "escalate_below_confidence": 0.4}}
        )
        assert r.status_code == 200 and r.json()["overridden"] == ["escalate_below_confidence", "tier1_model"]
        assert c.get("/api/health").json()["tier1"] == "claude-sonnet-5-5"

        provider.calls.clear()
        c.post("/api/postings", json={"job_id": "b", "title": "t"})
        assert [r.model for r in provider.calls] == ["claude-sonnet-5-5"]  # 0.5 >= 0.4: no escalation

        # Setting a value back to its env default removes the override.
        c.put("/api/settings", json={"values": {"tier1_model": "fake-small"}})
        assert c.get("/api/settings").json()["overridden"] == ["escalate_below_confidence"]
        assert c.delete("/api/settings").json()["overridden"] == []


def test_settings_are_validated(settings):
    with TestClient(create_app(settings, FakeProvider())) as c:
        assert c.put("/api/settings", json={"values": {"escalate_below_confidence": 2}}).status_code == 422
        assert c.put("/api/settings", json={"values": {"tier1_effort": "turbo"}}).status_code == 422
        assert c.put("/api/settings", json={"values": {"database_url": "x"}}).status_code == 422  # not run-time
        assert c.put("/api/settings", json={"values": {"tier1_model": ""}}).status_code == 422


def test_workers_see_a_change_within_the_cache_window(settings):
    with TestClient(create_app(settings, FakeProvider())) as c:
        svc = c.app.state.service
        assert svc.settings().backfill_days == 30
        svc.db.set_app_settings({"backfill_days": 7})  # as if another process wrote it
        svc.config._cached = (svc.config._cached[0] - CACHE_S - 1, svc.config._cached[1])
        assert svc.settings().backfill_days == 7


def test_publish_with_backfill_and_rollback_reuses_old_labels(settings):
    provider = FakeProvider(policy_aware_handler)
    with TestClient(create_app(settings, provider)) as c:
        wait_job(c, c.post("/api/postings/batch", json={"postings": [CLERICAL, MULE]}).json()["job_id"])
        c.post("/api/policies", json={"source": (REPO_ROOT / "policies" / "v2.yaml").read_text()})

        preview = c.get("/api/policies/v2/relabel/preview?scope=recent&days=30").json()
        assert preview["count"] == 2

        pub = c.post("/api/policies/v2/publish", json={"backfill": True, "days": 30}).json()
        assert pub["status"] == "published" and pub["backfill_job_id"] is not None
        assert wait_job(c, pub["backfill_job_id"])["done"] == 2
        assert c.get("/api/results/c1").json()["policy_sections"] == ["§6"]

        # Roll back: re-publish the retired v1. Its labels are still stored, so nothing is re-labeled.
        assert {p["version"]: p["status"] for p in c.get("/api/policies").json()}["v1"] == "retired"
        provider.calls.clear()
        back = c.post("/api/policies/v1/publish", json={"backfill": True}).json()
        assert back["status"] == "published"
        assert wait_job(c, back["backfill_job_id"])["total"] == 0 and provider.calls == []
        r = c.get("/api/results/c1").json()
        assert r["policy_version"] == "v1" and r["label"] == "ALLOWED"
        assert c.post("/api/policies/v1/publish").status_code == 409  # already published


def test_publish_without_backfill_by_default(settings):
    with TestClient(create_app(settings, FakeProvider())) as c:
        c.post("/api/policies", json={"source": (REPO_ROOT / "policies" / "v2.yaml").read_text()})
        assert c.post("/api/policies/v2/publish").json()["backfill_job_id"] is None
        c.put("/api/settings", json={"values": {"backfill_on_publish": True}})
        assert c.post("/api/policies/v1/publish").json()["backfill_job_id"] is not None


def test_one_posting_under_several_versions(settings):
    with TestClient(create_app(settings, FakeProvider(policy_aware_handler))) as c:
        c.post("/api/postings", json=CLERICAL)
        c.post("/api/policies", json={"source": (REPO_ROOT / "policies" / "v2.yaml").read_text()})

        view = c.get("/api/postings/c1/versions?v=v1&v=v2").json()["versions"]
        assert [(x["policy_version"], x["status"]) for x in view] == [("v1", "published"), ("v2", "draft")]
        assert view[0]["result"]["label"] == "ALLOWED" and view[1]["result"] is None
        assert view[0]["labeled_at"] and view[1]["labeled_at"] is None

        # Label just this posting under the draft, from the case view.
        job = c.post("/api/policies/v2/relabel", json={"scope": "ids", "ids": ["c1"]}).json()
        wait_job(c, job["job_id"])
        view = c.get("/api/postings/c1/versions?v=v1&v=v2").json()["versions"]
        assert view[1]["result"]["label"] == "NEEDS_REVIEW" and view[1]["result"]["trace"]["steps"]
        assert view[1]["labeled_at"] >= view[0]["labeled_at"]

        assert c.get("/api/postings/c1/versions?v=v9").status_code == 404
        assert c.get("/api/postings/nope/versions?v=v1").status_code == 404
        assert c.post("/api/policies/v2/relabel", json={"scope": "ids", "ids": []}).status_code == 422


def test_backfill_reaches_only_postings_with_a_decision(settings):
    from labeler.labeling.schemas import Posting

    with TestClient(create_app(settings, FakeProvider(policy_aware_handler))) as c:
        svc = c.app.state.service
        # 50 dataset rows loaded but never labeled, as `make data` leaves them.
        svc.db.upsert_postings([(Posting(job_id=f"d{i}", title="t"), 0, False) for i in range(50)])
        c.post("/api/postings", json=CLERICAL)  # one decided posting
        c.post("/api/policies", json={"source": (REPO_ROOT / "policies" / "v2.yaml").read_text()})
        assert c.get("/api/policies/v2/relabel/preview?days=30").json()["count"] == 1


def test_only_decisions_under_the_published_version_reach_the_marketplace(settings):
    with TestClient(create_app(settings, FakeProvider(policy_aware_handler))) as c:
        c.post("/api/postings", json={"job_id": "e1", "title": "Clerk", "description": "Data entry from home."})
        assert [e["job_id"] for e in c.get("/api/events").json()["items"]] == ["e1"]  # v1 is published

        c.post("/api/policies", json={"source": (REPO_ROOT / "policies" / "v2.yaml").read_text()})
        job = c.post("/api/policies/v2/relabel", json={"scope": "ids", "ids": ["e1"]}).json()["job_id"]
        wait_job(c, job)
        assert c.get("/api/postings/e1/versions?v=v2").json()["versions"][0]["result"] is not None
        assert len(c.get("/api/events").json()["items"]) == 1  # the draft's shadow decision is not an event


def test_a_draft_cannot_change_while_it_is_being_labeled_and_labels_record_its_content(settings):
    no_worker = settings.model_copy(update={"embedded_worker": False})
    with TestClient(create_app(no_worker, FakeProvider(policy_aware_handler))) as c:
        src = (REPO_ROOT / "policies" / "v2.yaml").read_text()
        c.post("/api/policies", json={"source": src})
        c.post("/api/postings", json={"job_id": "d1", "title": "Clerk", "description": "Data entry."})
        job = c.post("/api/policies/v2/relabel", json={"scope": "ids", "ids": ["d1"]}).json()["job_id"]
        r = c.put("/api/policies/v2", json={"source": src.replace("Recruitment", "Hiring", 1)})
        assert r.status_code == 409 and f"#{job}" in r.json()["detail"]
        c.post(f"/api/jobs/{job}/cancel")
        assert c.put("/api/policies/v2", json={"source": src.replace("Recruitment", "Hiring", 1)}).status_code == 200

        import asyncio

        from labeler.labeling.schemas import Posting

        svc = c.app.state.service
        result = asyncio.run(svc.agent.label(Posting(job_id="d2", title="Clerk", description="x"), svc.policy("v2")))
        assert len(result.trace["policy_sha"]) == 12


def test_publish_is_the_commit_point_even_if_the_backfill_cannot_start(settings):
    with TestClient(create_app(settings, FakeProvider(policy_aware_handler))) as c:
        c.post("/api/policies", json={"source": (REPO_ROOT / "policies" / "v2.yaml").read_text()})

        async def queue_down(*a, **k):
            raise ConnectionError("queue unreachable")

        c.app.state.service.start_relabel = queue_down
        r = c.post("/api/policies/v2/publish", json={"backfill": True, "days": 30})
        assert r.status_code == 200 and r.json()["status"] == "published" and r.json()["backfill_job_id"] is None
        assert "backfill did not start" in r.json()["backfill_error"]
        assert c.get("/api/health").json()["published_policy"] == "v2"


def test_relabel_by_ids_reports_missing_postings(settings):
    with TestClient(create_app(settings, FakeProvider(policy_aware_handler))) as c:
        r = c.post("/api/policies/v1/relabel", json={"scope": "ids", "ids": ["nope-1", "nope-2"]})
        assert r.status_code == 404 and "nope-1" in r.json()["detail"]


def test_eval_runs_are_listed_and_cost_basis_is_reported(settings):
    from labeler.labeling.schemas import Posting

    with TestClient(create_app(settings, FakeProvider(policy_aware_handler))) as c:
        svc = c.app.state.service
        svc.db.upsert_postings([(Posting(job_id="e9", title="Clerk", description="Data entry."), 0, True)])
        job = c.post("/api/policies/v1/relabel", json={"scope": "eval"}).json()["job_id"]
        wait_job(c, job)
        assert {"version": "v1", "eval_decisions": 1} in c.get("/api/metrics/runs").json()
        m = c.get("/api/metrics?policy_version=v1").json()
        assert m["cost_basis"] == ["api"]  # the scripted provider is not the CLI

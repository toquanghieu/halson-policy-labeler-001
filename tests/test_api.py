"""End-to-end through HTTP with a scripted model: the policy-change demo as a test."""

import time

from fastapi.testclient import TestClient

from labeler.config import REPO_ROOT
from labeler.infra.llm.fake_adapter import FakeProvider
from labeler_api.main import create_app


def policy_aware_handler(req):
    """Stands in for a model that reads the policy: it can only report sections
    that the schema (built from the policy) offers."""
    offered = req.schema["properties"]["findings"]["items"]["properties"]["section"]["enum"]
    findings = []
    if "§6" in offered and "data entry" in req.user.lower():
        findings.append({"section": "§6", "quotes": ["home-based data entry"], "facts": [], "explanation": "x"})
    if "western union" in req.user.lower():
        findings.append(
            {"section": "§3", "quotes": ["forward them via Western Union"], "facts": [], "explanation": "x"}
        )
    return {"findings": findings, "insufficient_evidence": False, "rationale": "r", "confidence": 0.9}


def wait_job(client, job_id, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "failed"):
            return job
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def test_label_batch_publish_v2_and_relabel(settings):
    app = create_app(settings, FakeProvider(policy_aware_handler))
    with TestClient(app) as c:
        assert c.get("/api/health").json()["published_policy"] == "v1"

        clerical = {"job_id": "c1", "title": "Clerk", "description": "Easy home-based data entry, start today."}
        mule = {"job_id": "m1", "title": "Agent", "description": "Receive checks and forward them via Western Union."}

        r = c.post("/api/postings", json=clerical).json()
        assert r["label"] == "ALLOWED" and r["policy_version"] == "v1"

        job = c.post("/api/postings/batch", json={"postings": [clerical, mule]}).json()
        assert wait_job(c, job["job_id"])["done"] == 2
        public = c.get("/api/results/m1").json()
        assert set(public) == {
            "job_id",
            "label",
            "policy_sections",
            "evidence",
            "rationale",
            "confidence",
            "policy_version",
        }
        assert public["label"] == "VIOLATING" and public["evidence"] == ["forward them via Western Union"]

        # Publish v2 (adds §6) through the API: no code change, no restart.
        v2 = (REPO_ROOT / "policies" / "v2.yaml").read_text()
        assert c.post("/api/policies", json={"source": v2}).status_code == 201
        assert c.post("/api/policies", json={"source": v2}).status_code == 409  # versions are immutable ids

        # Shadow-run the draft before publishing; the published version keeps serving.
        shadow = c.post("/api/policies/v2/relabel", json={"scope": "previous"}).json()
        wait_job(c, shadow["job_id"])
        assert c.get("/api/results/c1").json()["label"] == "ALLOWED"
        assert c.get("/api/results/c1?policy_version=v2").json()["label"] == "NEEDS_REVIEW"

        assert c.post("/api/policies/v2/publish").status_code == 200
        r = c.get("/api/results/c1").json()
        assert r["policy_version"] == "v2" and r["policy_sections"] == ["§6"]

        diff = c.get("/api/compare?a=v1&b=v2").json()
        assert diff["transitions"]["ALLOWED->NEEDS_REVIEW"] == 1
        assert [d["job_id"] for d in diff["changed"]] == ["c1"]

        statuses = {p["version"]: p["status"] for p in c.get("/api/policies").json()}
        assert statuses == {"v1": "retired", "v2": "published"}

        # Filter by the new section.
        listed = c.get("/api/labels?section=%C2%A76").json()
        assert [i["job_id"] for i in listed["items"]] == ["c1"]


def test_invalid_policy_is_rejected_with_reason(settings):
    with TestClient(create_app(settings, FakeProvider())) as c:
        r = c.post("/api/policies", json={"source": "version: v3\ntitle: x\nsections: []"})
        assert r.status_code == 422 and "at least 1" in r.json()["detail"]
        assert c.post("/api/policies/validate", json={"source": "a: [b"}).json()["ok"] is False


def test_published_policy_is_immutable(settings):
    with TestClient(create_app(settings, FakeProvider())) as c:
        src = (REPO_ROOT / "policies" / "v1.yaml").read_text()
        assert c.put("/api/policies/v1", json={"source": src}).status_code == 409


def test_unknown_versions_and_postings_are_404(settings):
    with TestClient(create_app(settings, FakeProvider())) as c:
        assert c.get("/api/policies/v9").status_code == 404
        assert c.post("/api/postings?policy_version=v9", json={"job_id": "x", "title": "t"}).status_code == 404
        assert c.get("/api/results/nope").status_code == 404
        assert c.post("/api/policies/v9/relabel", json={"scope": "eval"}).status_code == 404
        assert c.post("/api/policies/v1/relabel", json={"scope": "everything"}).status_code == 422


def test_prometheus_metrics_endpoint(settings):
    with TestClient(create_app(settings, FakeProvider())) as c:
        c.post("/api/postings", json={"job_id": "m-1", "title": "t"})
        r = c.get("/metrics")
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
        assert 'labeler_decisions_total{label="ALLOWED",policy_version="v1",tier="tier1"}' in r.text


def test_unknown_api_path_is_404_json_not_the_ui_shell(settings, tmp_path, monkeypatch):
    import labeler_api.ui as ui

    dist = tmp_path / "frontend" / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html>")
    monkeypatch.setattr(ui, "REPO_ROOT", tmp_path)
    with TestClient(create_app(settings, FakeProvider())) as c:
        r = c.get("/api/settings/nope")
        assert r.status_code == 404 and r.json()["detail"] == "no such endpoint: /api/settings/nope"
        assert c.get("/queue").text == "<!doctype html>"  # client-side routes still get the UI


def test_the_api_never_calls_the_model_itself(settings):
    """POST /api/postings goes through the queue: with no worker it answers 202 and a job id,
    and no model call was made by the API process."""
    from labeler.infra.llm.fake_adapter import FakeProvider as Fake

    fake = Fake(policy_aware_handler)
    quick = settings.model_copy(update={"embedded_worker": False, "sync_label_wait_s": 0.3})
    with TestClient(create_app(quick, fake)) as c:
        r = c.post("/api/postings", json={"job_id": "w1", "title": "Clerk", "description": "Data entry."})
        assert r.status_code == 202 and fake.calls == []
        body = r.json()
        assert body["status_url"] == f"/api/jobs/{body['job_id']}" and body["result_url"] == "/api/results/w1"
        job = c.get(body["status_url"]).json()
        assert (job["kind"], job["total"], job["queue"]) == ("single", 1, "sync")  # ahead of batches


def test_sync_endpoint_returns_the_workers_decision(settings):
    with TestClient(create_app(settings, FakeProvider(policy_aware_handler))) as c:  # embedded worker
        r = c.post("/api/postings", json={"job_id": "w2", "title": "Clerk", "description": "Data entry from home."})
        assert r.status_code == 200 and r.json()["job_id"] == "w2" and r.json()["label"]
        jobs = c.get("/api/jobs").json()
        assert jobs[0]["kind"] == "single" and jobs[0]["status"] == "done"  # it went through the queue

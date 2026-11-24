"""Messages tab: import without labeling, list what is unlabeled, label it, it moves to Decisions."""

from fastapi.testclient import TestClient

from labeler.infra.llm.fake_adapter import FakeProvider
from labeler.labeling.schemas import Posting
from labeler_api.main import create_app
from tests.test_api import policy_aware_handler, wait_job


def test_import_list_label_moves_to_decisions(settings):
    with TestClient(create_app(settings, FakeProvider(policy_aware_handler))) as c:
        svc = c.app.state.service
        # Two eval postings and one dataset row, as `make data` would load them.
        svc.db.upsert_postings(
            [
                (Posting(job_id="e1", title="Eval one"), 0, True),
                (Posting(job_id="e2", title="Eval two"), 1, True),
                (Posting(job_id="d1", title="Dev row"), 0, False),
            ]
        )
        r = c.post(
            "/api/inbox/import",
            json={"postings": [{"job_id": "m1", "title": "Agent", "description": "forward them via Western Union"}]},
        )
        assert r.status_code == 201 and r.json() == {"stored": 1, "new": 1, "updated": 0}
        assert c.get("/api/labels").json()["total"] == 0  # importing does not label

        inbox = c.get("/api/inbox").json()
        assert inbox["policy_version"] == "v1"
        assert [i["job_id"] for i in inbox["items"]] == ["m1", "e1", "e2"]  # imported first; dev rows not in inbox
        assert inbox["counts"] == {"inbox": 3, "eval": 2, "imported": 1, "all": 4}
        assert [i["source"] for i in inbox["items"]] == ["imported", "eval set", "eval set"]

        job = c.post("/api/inbox/label", json={"ids": ["m1"]}).json()
        assert job["count"] == 1 and wait_job(c, job["job_id"])["done"] == 1
        assert [i["job_id"] for i in c.get("/api/inbox").json()["items"]] == ["e1", "e2"]
        assert c.get("/api/labels").json()["items"][0]["job_id"] == "m1"

        job = c.post("/api/inbox/label", json={"all_in_scope": True, "scope": "eval"}).json()
        assert job["count"] == 2 and wait_job(c, job["job_id"])["done"] == 2
        assert c.get("/api/inbox").json()["total"] == 0


def test_inbox_errors_and_reimport_keeps_gold(settings):
    with TestClient(create_app(settings, FakeProvider())) as c:
        svc = c.app.state.service
        svc.db.upsert_postings([(Posting(job_id="e1", title="Eval"), 1, True)])
        r = c.post("/api/inbox/import", json={"postings": [{"job_id": "e1", "title": "Eval, edited"}]}).json()
        assert r == {"stored": 1, "new": 0, "updated": 1}
        assert svc.db.get_posting("e1")[1:] == (1, True)  # dataset label and eval flag survive
        dup = {"postings": [{"job_id": "x"}, {"job_id": "x"}]}
        assert c.post("/api/inbox/import", json=dup).status_code == 422
        assert c.post("/api/inbox/label", json={}).status_code == 422
        assert c.post("/api/inbox/label", json={"ids": ["nope"]}).status_code == 404
        assert c.get("/api/inbox?scope=weird").status_code == 422

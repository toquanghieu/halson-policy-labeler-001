"""Repository behaviour on both engines (SQLite and a real Postgres container)."""

from labeler.bootstrap import build_db
from labeler.config import Settings
from labeler.labeling.schemas import Label, LabelResult, Posting


def make_db(db_url, tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path, database_url=db_url)
    return build_db(settings)  # runs the Alembic migrations


def label(job_id, version="v1", sections=("§3", "§5"), lab=Label.VIOLATING):
    return LabelResult(
        job_id=job_id,
        label=lab,
        policy_sections=list(sections),
        evidence=["forward them via Western Union"],
        rationale="r",
        confidence=0.9,
        policy_version=version,
        trace={"tier": "tier1", "cost_usd": 0.001, "latency_ms": 900.0, "steps": [{"raw": {"big": "x" * 1000}}]},
    )


def test_upsert_keeps_gold_and_eval_flag(db_url, tmp_path):
    db = make_db(db_url, tmp_path)
    db.upsert_postings([(Posting(job_id="89", title="a"), 1, True)])
    db.upsert_postings([(Posting(job_id="89", title="b"), None, False)])  # a later submission of the same id
    posting, gold, is_eval = db.get_posting("89")
    assert posting.title == "b" and gold == 1 and is_eval is True


def test_ids_sort_numerically_and_tolerate_non_numeric(db_url, tmp_path):
    db = make_db(db_url, tmp_path)
    db.upsert_postings([(Posting(job_id=j), None, False) for j in ("17857", "89", "177", "demo-1")])
    assert db.posting_ids()[:3] == ["89", "177", "17857"]


def test_label_trace_goes_to_object_store_and_row_keeps_summary(db_url, tmp_path):
    db = make_db(db_url, tmp_path)
    db.upsert_postings([(Posting(job_id="1"), 1, True)])
    db.save_label(label("1"))
    summary = db.get_label("1", "v1", full_trace=False)
    full = db.get_label("1", "v1")
    assert "steps" not in summary.trace and summary.trace["tier"] == "tier1"
    assert full.trace["steps"][0]["raw"]["big"] == "x" * 1000
    assert (tmp_path / "objects" / "traces" / "v1" / "1.json").exists()


def test_section_filter_uses_label_sections_table(db_url, tmp_path):
    db = make_db(db_url, tmp_path)
    db.upsert_postings([(Posting(job_id=j), 0, True) for j in ("1", "2")])
    db.save_label(label("1", sections=("§3", "§5")))
    db.save_label(label("2", sections=("§5",), lab=Label.NEEDS_REVIEW))
    assert db.query_labels("v1", section="§3")[0] == 1
    assert db.query_labels("v1", section="§5")[0] == 2
    db.save_label(label("1", sections=("§2",)))  # re-label replaces the section rows
    assert db.query_labels("v1", section="§3")[0] == 0
    assert db.label_rows("v1")[0]["sections"] in (["§2"], ["§5"])


def test_job_items_are_idempotent_and_close_the_job(db_url, tmp_path):
    db = make_db(db_url, tmp_path)
    job = db.create_job("batch", "v1", ["a", "b", "a"])  # duplicates collapse
    assert db.get_job(job)["total"] == 3 and db.get_job(job)["status"] == "queued"
    assert db.mark_item(job, "a", failed=False) is True
    assert db.mark_item(job, "a", failed=True) is False  # redelivered message: no double count
    assert db.get_job(job)["status"] == "running"
    db.mark_item(job, "b", failed=True)
    j = db.get_job(job)
    assert (j["status"], j["done"], j["failed"]) == ("done", 1, 1) and j["finished_at"]


def test_outbox_event_written_with_label(db_url, tmp_path):
    db = make_db(db_url, tmp_path)
    db.upsert_postings([(Posting(job_id="1"), None, False)])
    db.save_label(label("1"))
    db.save_label(label("1", version="v2"), emit_event=False)  # backfill import: no event
    events = db.events_after(0)
    assert [(e["type"], e["job_id"], e["payload"]["label"]) for e in events] == [("PostingLabeled", "1", "VIOLATING")]
    assert set(events[0]["payload"]) == {
        "job_id",
        "label",
        "policy_sections",
        "evidence",
        "rationale",
        "confidence",
        "policy_version",
    }


def test_publish_retires_previous_version(db_url, tmp_path):
    db = make_db(db_url, tmp_path)
    db.insert_policy("v1", "src1", "published")
    db.insert_policy("v2", "src2", "draft")
    db.publish_policy("v2")
    assert {p["version"]: p["status"] for p in db.list_policies()} == {"v1": "retired", "v2": "published"}


def test_concurrent_workers_finishing_a_job_close_it(db_url, tmp_path):
    """Two workers recording a job's last items at the same moment: one of them must close it.

    Without a lock on the job row, each transaction counts the other's item as still pending
    (READ COMMITTED), neither sees 0, and the job stays "running" forever.
    """
    import threading

    db = make_db(db_url, tmp_path)
    jobs = [db.create_job("batch", "v1", [f"{n}-a", f"{n}-b"]) for n in range(15)]
    for job in jobs:
        start = threading.Barrier(2)

        def finish(item, job=job, start=start):
            start.wait()
            db.mark_item(job, item, failed=False)

        n = jobs.index(job)
        threads = [threading.Thread(target=finish, args=(f"{n}-{x}",)) for x in "ab"]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    assert [db.get_job(j)["status"] for j in jobs] == ["done"] * len(jobs)


def test_jobs_left_running_without_pending_items_are_closed(db_url, tmp_path):
    from sqlalchemy import update

    from labeler.infra.db.tables import job_items, jobs

    db = make_db(db_url, tmp_path)
    stuck, live = db.create_job("batch", "v1", ["a"]), db.create_job("batch", "v1", ["b"])
    with db.tx() as c:  # what the old race left behind: item recorded, job still running
        c.execute(update(job_items).where(job_items.c.job_id == stuck).values(status="done"))
        c.execute(update(jobs).where(jobs.c.id == stuck).values(status="running"))
    assert db.close_finished_jobs() == [stuck]
    assert db.get_job(stuck)["status"] == "done" and db.get_job(live)["status"] == "queued"
    assert db.close_finished_jobs() == []


def test_a_job_item_lease_admits_one_worker_and_expires(db_url, tmp_path):
    db = make_db(db_url, tmp_path)
    job = db.create_job("batch", "v1", ["a"])
    assert db.claim_item(job, "a", 300) is True
    assert db.claim_item(job, "a", 300) is False  # a second delivery while the first holds it
    db.release_item(job, "a")
    assert db.claim_item(job, "a", -1) is True  # released: claimable again (this lease is already over)
    assert db.claim_item(job, "a", 300) is True  # an expired lease (a crashed worker) can be taken
    db.mark_item(job, "a", failed=False)
    assert db.claim_item(job, "a", 300) is False  # done items are never claimed

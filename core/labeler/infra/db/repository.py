"""Data access (repository). The only module that writes SQL.

`DATABASE_URL` picks the engine: Postgres in compose and production (Aurora), SQLite for
tests and no-infra development. Full decision traces live in the object store; a label row
keeps a summary and the key.
"""

from __future__ import annotations

import html
import json
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import (
    Connection,
    Engine,
    case,
    create_engine,
    delete,
    event,
    exists,
    func,
    insert,
    or_,
    select,
    update,
)
from sqlalchemy.dialects import postgresql, sqlite

from labeler.infra.db.tables import (
    app_settings,
    events,
    job_items,
    jobs,
    label_sections,
    label_status,
    labels,
    policies,
    postings,
    secrets,
    workers,
)
from labeler.infra.storage.port import ObjectStore, get_json, put_json
from labeler.labeling.schemas import LabelResult, Posting

# Kept in the label row so list views, metrics and comparisons never touch the object store.
TRACE_SUMMARY_KEYS = (
    "tier",
    "cost_usd",
    "cost_unpriced",
    "cost_basis",
    "latency_ms",
    "router_score",
    "escalation_reasons",
)


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def make_engine(url: str, pool_size: int = 10) -> Engine:
    if url.startswith("sqlite"):
        engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):  # WAL: readers don't block the writer
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        return engine
    return create_engine(url, pool_size=pool_size, max_overflow=pool_size, pool_pre_ping=True)


def _numeric_order(col):
    """Order ids like '89' < '177' < '17857' without CAST (ids may be non-numeric)."""
    return (func.length(col), col)


class DB:
    def __init__(self, engine: Engine, store: ObjectStore):
        self.engine = engine
        self.store = store
        self._dialect = engine.dialect.name

    def close(self) -> None:
        self.engine.dispose()

    @contextmanager
    def tx(self) -> Iterator[Connection]:
        with self.engine.begin() as c:
            yield c

    # ---- postings -------------------------------------------------------
    def _insert(self, table):
        return (postgresql.insert if self._dialect == "postgresql" else sqlite.insert)(table)

    def upsert_postings(self, rows: Iterable[tuple[Posting, int | None, bool]], chunk: int = 1000) -> None:
        batch: list[dict[str, Any]] = []
        for p, gold, is_eval in rows:
            batch.append(
                {
                    "job_id": p.job_id,
                    "title": p.title,
                    "location": p.location,
                    "data": p.model_dump_json(),
                    "gold": gold,
                    "is_eval": bool(is_eval),
                    "created_at": now(),
                }
            )
            if len(batch) >= chunk:
                self._upsert_posting_batch(batch)
                batch = []
        if batch:
            self._upsert_posting_batch(batch)

    def _upsert_posting_batch(self, batch: list[dict[str, Any]]) -> None:
        stmt = self._insert(postings).values(batch)
        stmt = stmt.on_conflict_do_update(
            index_elements=[postings.c.job_id],
            set_={
                "title": stmt.excluded.title,
                "location": stmt.excluded.location,
                "data": stmt.excluded.data,
                "gold": func.coalesce(stmt.excluded.gold, postings.c.gold),
                "is_eval": or_(stmt.excluded.is_eval, postings.c.is_eval),
            },
        )
        with self.tx() as c:
            c.execute(stmt)

    def count(self, table_name: str) -> int:
        table = {"postings": postings, "labels": labels, "policies": policies}[table_name]
        with self.tx() as c:
            return int(c.scalar(select(func.count()).select_from(table)) or 0)

    def get_posting(self, job_id: str) -> tuple[Posting, int | None, bool] | None:
        with self.tx() as c:
            r = c.execute(
                select(postings.c.data, postings.c.gold, postings.c.is_eval).where(postings.c.job_id == job_id)
            ).first()
        if not r:
            return None
        return Posting.model_validate_json(r.data), r.gold, bool(r.is_eval)

    def posting_ids(self, *, eval_only: bool = False, since: str | None = None, limit: int | None = None) -> list[str]:
        q = select(postings.c.job_id)
        if eval_only:
            q = q.where(postings.c.is_eval.is_(True))
        if since:
            q = q.where(postings.c.created_at >= since)
        q = q.order_by(*_numeric_order(postings.c.job_id))
        if limit:
            q = q.limit(limit)
        with self.tx() as c:
            return list(c.scalars(q))

    def ids_decided_since(self, since: str) -> list[str]:
        """Postings with a stored decision made at or after `since`, under any real policy version
        (not the `<version>@<tag>` ablation and teacher runs)."""
        q = (
            select(labels.c.job_id)
            .where(labels.c.created_at >= since, ~labels.c.policy_version.contains("@"))
            .distinct()
        )
        with self.tx() as c:
            return sorted(c.scalars(q), key=lambda x: (len(x), x))

    def ids_labeled_under(self, version: str) -> list[str]:
        with self.tx() as c:
            return list(c.scalars(select(labels.c.job_id).where(labels.c.policy_version == version)))

    # ---- policies -------------------------------------------------------
    def insert_policy(self, version: str, source: str, status: str, note: str = "") -> None:
        with self.tx() as c:
            c.execute(
                insert(policies).values(
                    version=version,
                    source=source,
                    status=status,
                    note=note,
                    created_at=now(),
                    published_at=now() if status == "published" else None,
                )
            )

    def update_policy_source(self, version: str, source: str, note: str) -> None:
        with self.tx() as c:
            c.execute(
                update(policies)
                .where(policies.c.version == version, policies.c.status == "draft")
                .values(source=source, note=note)
            )

    def publish_policy(self, version: str) -> None:
        """Exactly one published version at a time; the previous one is retired, not deleted."""
        with self.tx() as c:
            c.execute(update(policies).where(policies.c.status == "published").values(status="retired"))
            c.execute(
                update(policies).where(policies.c.version == version).values(status="published", published_at=now())
            )

    def delete_draft(self, version: str) -> bool:
        with self.tx() as c:
            res = c.execute(delete(policies).where(policies.c.version == version, policies.c.status == "draft"))
            return res.rowcount > 0

    def get_policy_row(self, version: str) -> dict[str, Any] | None:
        with self.tx() as c:
            r = c.execute(select(policies).where(policies.c.version == version)).mappings().first()
        return dict(r) if r else None

    def published_policy_row(self) -> dict[str, Any] | None:
        with self.tx() as c:
            r = c.execute(select(policies).where(policies.c.status == "published")).mappings().first()
        return dict(r) if r else None

    def list_policies(self) -> list[dict[str, Any]]:
        labeled = (
            select(func.count())
            .select_from(labels)
            .where(labels.c.policy_version == policies.c.version)
            .scalar_subquery()
            .label("labeled")
        )
        with self.tx() as c:
            rows = c.execute(select(policies, labeled).order_by(policies.c.created_at, policies.c.version)).mappings()
            return [dict(r) for r in rows]

    # ---- labels ---------------------------------------------------------
    @staticmethod
    def trace_key(job_id: str, version: str) -> str:
        return f"traces/{version}/{job_id}.json"

    def save_label(self, r: LabelResult, *, emit_event: bool = True, created_at: str | None = None) -> None:
        """Label row, its sections and a PostingLabeled outbox event in one transaction.
        The full trace goes to the object store first, so a row never points at nothing."""
        key = self.trace_key(r.job_id, r.policy_version)
        put_json(self.store, key, r.trace)
        summary = {k: r.trace[k] for k in TRACE_SUMMARY_KEYS if k in r.trace}
        row = {
            "label": r.label.value,
            "confidence": r.confidence,
            "tier": r.trace.get("tier", ""),
            "cost_usd": r.trace.get("cost_usd", 0.0),
            "latency_ms": r.trace.get("latency_ms", 0.0),
            "result": r.model_copy(update={"trace": summary}).model_dump_json(),
            "trace_key": key,
            "created_at": created_at or now(),
        }
        stmt = self._insert(labels).values(job_id=r.job_id, policy_version=r.policy_version, **row)
        stmt = stmt.on_conflict_do_update(index_elements=[labels.c.job_id, labels.c.policy_version], set_=row)
        failed = r.trace.get("tier") == "failed"
        error = next((s.get("error", "") for s in reversed(r.trace.get("steps", [])) if s.get("error")), "")
        with self.tx() as c:
            c.execute(stmt)
            self._set_status(
                c, [r.job_id], r.policy_version, "failed" if failed else "done", error=error if failed else ""
            )
            c.execute(
                delete(label_sections).where(
                    label_sections.c.job_id == r.job_id, label_sections.c.policy_version == r.policy_version
                )
            )
            if r.policy_sections:
                c.execute(
                    insert(label_sections),
                    [
                        {"job_id": r.job_id, "policy_version": r.policy_version, "section": s}
                        for s in dict.fromkeys(r.policy_sections)
                    ],
                )
            if not emit_event:
                return
            c.execute(
                insert(events).values(
                    type="PostingLabeled",
                    job_id=r.job_id,
                    policy_version=r.policy_version,
                    payload=json.dumps(r.public(), ensure_ascii=False),
                    created_at=now(),
                )
            )

    def get_label(self, job_id: str, version: str, *, full_trace: bool = True) -> LabelResult | None:
        with self.tx() as c:
            r = c.execute(
                select(labels.c.result, labels.c.trace_key).where(
                    labels.c.job_id == job_id, labels.c.policy_version == version
                )
            ).first()
        if not r:
            return None
        result = LabelResult.model_validate_json(r.result)
        if full_trace:
            trace = get_json(self.store, r.trace_key)
            if trace is not None:
                result = result.model_copy(update={"trace": trace})
        return result

    def _sections_by_job(self, c: Connection, version: str, job_ids: list[str] | None = None) -> dict[str, list[str]]:
        q = select(label_sections.c.job_id, label_sections.c.section).where(label_sections.c.policy_version == version)
        if job_ids is not None:
            q = q.where(label_sections.c.job_id.in_(job_ids))
        out: dict[str, list[str]] = {}
        for jid, sec in c.execute(q.order_by(label_sections.c.section)):
            out.setdefault(jid, []).append(sec)
        return out

    def label_versions(self, job_id: str) -> list[dict[str, Any]]:
        with self.tx() as c:
            rows = c.execute(
                select(labels.c.policy_version, labels.c.label, labels.c.confidence, labels.c.created_at)
                .where(labels.c.job_id == job_id)
                .order_by(labels.c.created_at)
            ).mappings()
            rows = [dict(r) for r in rows]
            by_version: dict[str, list[str]] = {}
            for v, s in c.execute(
                select(label_sections.c.policy_version, label_sections.c.section)
                .where(label_sections.c.job_id == job_id)
                .order_by(label_sections.c.section)
            ):
                by_version.setdefault(v, []).append(s)
        return [{**r, "sections": by_version.get(r["policy_version"], [])} for r in rows]

    # ---- inbox: postings not yet labeled under a version --------------------------
    _INBOX_SCOPES = ("inbox", "eval", "imported", "all")

    def _scope_cond(self, scope: str):
        if scope == "eval":
            return postings.c.is_eval.is_(True)
        if scope == "imported":  # submitted through the API or the Messages tab, not from EMSCAD
            return postings.c.gold.is_(None) & postings.c.is_eval.is_(False)
        if scope == "inbox":  # the brief's 400 + everything imported
            return or_(postings.c.is_eval.is_(True), postings.c.gold.is_(None))
        return None

    def _unlabeled(self, version: str):
        return ~exists().where(labels.c.job_id == postings.c.job_id, labels.c.policy_version == version)

    def query_unlabeled(
        self, version: str, *, scope: str = "inbox", q: str | None = None, limit: int = 50, offset: int = 0
    ) -> tuple[int, dict[str, int], list[dict[str, Any]]]:
        """Postings with no decision under `version`: the Messages tab. Labeled ones move to Decisions."""
        conds = [self._unlabeled(version)]
        if (sc := self._scope_cond(scope)) is not None:
            conds.append(sc)
        if q:
            conds.append(or_(func.lower(postings.c.data).contains(q.lower(), autoescape=True), postings.c.job_id == q))
        imported = postings.c.gold.is_(None) & postings.c.is_eval.is_(False)
        imported_first = case((imported, 0), else_=1)
        newest_import = case((imported, postings.c.created_at), else_="")  # imports newest first, the rest by id
        with self.tx() as c:
            total = c.scalar(select(func.count()).select_from(postings).where(*conds)) or 0
            counts = {
                s: int(
                    c.scalar(
                        select(func.count())
                        .select_from(postings)
                        .where(self._unlabeled(version), *([x] if (x := self._scope_cond(s)) is not None else []))
                    )
                    or 0
                )
                for s in self._INBOX_SCOPES
            }
            rows = c.execute(
                select(
                    postings.c.job_id,
                    postings.c.title,
                    postings.c.location,
                    postings.c.data,
                    postings.c.gold,
                    postings.c.is_eval,
                    postings.c.created_at,
                )
                .where(*conds)
                .order_by(imported_first, newest_import.desc(), *_numeric_order(postings.c.job_id))
                .limit(limit)
                .offset(offset)
            ).all()
        states = self.statuses_for([r.job_id for r in rows], version)
        items = []
        for r in rows:
            d = json.loads(r.data)
            text = " ".join(html.unescape(d.get("description") or "").split())
            items.append(
                {
                    "job_id": r.job_id,
                    "title": r.title,
                    "location": r.location,
                    "snippet": text[:180] + ("…" if len(text) > 180 else ""),
                    "has_company_profile": bool((d.get("company_profile") or "").strip()),
                    "gold": r.gold,
                    "is_eval": bool(r.is_eval),
                    "source": "eval set" if r.is_eval else ("imported" if r.gold is None else "dataset"),
                    "created_at": r.created_at,
                    "state": states.get(r.job_id),  # None: never requested; queued / labeling / failed
                }
            )
        return int(total), counts, items

    def unlabeled_ids(
        self, version: str, *, scope: str = "inbox", q: str | None = None, limit: int = 5000
    ) -> list[str]:
        conds = [self._unlabeled(version)]
        if (sc := self._scope_cond(scope)) is not None:
            conds.append(sc)
        if q:
            conds.append(or_(func.lower(postings.c.data).contains(q.lower(), autoescape=True), postings.c.job_id == q))
        with self.tx() as c:
            return list(
                c.scalars(
                    select(postings.c.job_id).where(*conds).order_by(*_numeric_order(postings.c.job_id)).limit(limit)
                )
            )

    def existing_ids(self, ids: list[str]) -> set[str]:
        out: set[str] = set()
        with self.tx() as c:
            for i in range(0, len(ids), 1000):
                out |= set(c.scalars(select(postings.c.job_id).where(postings.c.job_id.in_(ids[i : i + 1000]))))
        return out

    def query_labels(
        self,
        version: str,
        *,
        label: str | None = None,
        section: str | None = None,
        tier: str | None = None,
        eval_only: bool = False,
        disagree: bool = False,
        q: str | None = None,
        order: str = "risk",
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[int, list[dict[str, Any]]]:
        conds = [labels.c.policy_version == version]
        if label:
            conds.append(labels.c.label == label)
        if section:
            conds.append(
                exists().where(
                    label_sections.c.job_id == labels.c.job_id,
                    label_sections.c.policy_version == labels.c.policy_version,
                    label_sections.c.section == section,
                )
            )
        if tier:
            conds.append(labels.c.tier == tier)
        if eval_only:
            conds.append(postings.c.is_eval.is_(True))
        if disagree:
            # gold says fraud but we allowed it, or gold says legit but we flagged it.
            conds.append(
                or_(
                    (postings.c.gold == 1) & (labels.c.label == "ALLOWED"),
                    (postings.c.gold == 0) & (labels.c.label == "VIOLATING"),
                )
            )
        if q:
            conds.append(or_(func.lower(postings.c.data).contains(q.lower(), autoescape=True), labels.c.job_id == q))
        risk = case((labels.c.label == "VIOLATING", 0), (labels.c.label == "NEEDS_REVIEW", 1), else_=2)
        order_by = {
            "risk": (risk, labels.c.confidence.asc()),
            "confidence": (labels.c.confidence.asc(),),
            "recent": (labels.c.created_at.desc(),),
            "id": _numeric_order(labels.c.job_id),
        }.get(order, (labels.c.created_at.desc(),))
        joined = labels.join(postings, postings.c.job_id == labels.c.job_id)
        with self.tx() as c:
            total = c.scalar(select(func.count()).select_from(joined).where(*conds)) or 0
            rows = c.execute(
                select(
                    labels.c.job_id,
                    labels.c.label,
                    labels.c.confidence,
                    labels.c.tier,
                    labels.c.result,
                    postings.c.title,
                    postings.c.location,
                    postings.c.gold,
                    postings.c.is_eval,
                )
                .select_from(joined)
                .where(*conds)
                .order_by(*order_by)
                .limit(limit)
                .offset(offset)
            ).all()
            secs = self._sections_by_job(c, version, [r.job_id for r in rows])
        out = []
        for r in rows:
            res = json.loads(r.result)
            out.append(
                {
                    "job_id": r.job_id,
                    "title": r.title,
                    "location": r.location,
                    "label": r.label,
                    "policy_sections": res.get("policy_sections") or secs.get(r.job_id, []),
                    "confidence": r.confidence,
                    "rationale": res.get("rationale", ""),
                    "tier": r.tier,
                    "gold": r.gold,
                    "is_eval": bool(r.is_eval),
                }
            )
        return total, out

    def eval_runs(self) -> list[dict[str, Any]]:
        """Every version or run tag (e.g. v1@current) with decisions on the eval set, and how many."""
        q = (
            select(labels.c.policy_version, func.count())
            .select_from(labels.join(postings, postings.c.job_id == labels.c.job_id))
            .where(postings.c.is_eval.is_(True))
            .group_by(labels.c.policy_version)
            .order_by(labels.c.policy_version)
        )
        with self.tx() as c:
            return [{"version": v, "eval_decisions": n} for v, n in c.execute(q).all()]

    def label_rows(self, version: str, *, eval_only: bool = False) -> list[dict[str, Any]]:
        q = (
            select(
                labels.c.job_id,
                labels.c.label,
                labels.c.confidence,
                labels.c.tier,
                labels.c.cost_usd,
                labels.c.latency_ms,
                labels.c.result,
                postings.c.gold,
            )
            .select_from(labels.join(postings, postings.c.job_id == labels.c.job_id))
            .where(labels.c.policy_version == version)
        )
        if eval_only:
            q = q.where(postings.c.is_eval.is_(True))
        with self.tx() as c:
            rows = [dict(r) for r in c.execute(q).mappings()]
            secs = self._sections_by_job(c, version)
        for r in rows:
            r["sections"] = secs.get(r["job_id"], [])
            summary = json.loads(r.pop("result") or "{}").get("trace", {})
            r["cost_unpriced"] = bool(summary.get("cost_unpriced"))
            r["cost_basis"] = summary.get("cost_basis", "api")
        return rows

    # ---- outbox ---------------------------------------------------------
    def events_after(self, after_id: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        with self.tx() as c:
            rows = c.execute(select(events).where(events.c.id > after_id).order_by(events.c.id).limit(limit)).mappings()
            return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]

    # ---- jobs -----------------------------------------------------------
    def create_job(self, kind: str, version: str, ids: list[str], *, queue: str = "live", detail: str = "") -> int:
        with self.tx() as c:
            job_id = c.execute(
                insert(jobs)
                .values(
                    kind=kind,
                    policy_version=version,
                    queue=queue,
                    status="queued" if ids else "done",
                    total=len(ids),
                    detail=detail,
                    created_at=now(),
                    finished_at=None if ids else now(),
                )
                .returning(jobs.c.id)
            ).scalar_one()
            for i in range(0, len(ids), 5000):
                c.execute(
                    insert(job_items),
                    [
                        {"job_id": job_id, "posting_id": p, "status": "pending"}
                        for p in dict.fromkeys(ids[i : i + 5000])
                    ],
                )
            self._set_status(c, list(dict.fromkeys(ids)), version, "queued", run_id=int(job_id))
        return int(job_id)

    def mark_item(self, job_id: int, posting_id: str, *, failed: bool, error: str = "") -> bool:
        """Record one item's outcome. Idempotent: a redelivered message changes nothing.
        The item that empties the job's pending set also closes the job."""
        with self.tx() as c:
            # Serialise item updates per job: two workers finishing its last two items at once would
            # otherwise each count the other's item as pending, and nobody would close the job.
            # (SQLite has no FOR UPDATE; it serialises writers anyway.)
            c.execute(select(jobs.c.id).where(jobs.c.id == job_id).with_for_update())
            res = c.execute(
                update(job_items)
                .where(
                    job_items.c.job_id == job_id, job_items.c.posting_id == posting_id, job_items.c.status == "pending"
                )
                .values(status="failed" if failed else "done")
            )
            if res.rowcount == 0:
                return False
            if failed and error:  # no label was written (missing posting, dead-lettered message)
                version = c.scalar(select(jobs.c.policy_version).where(jobs.c.id == job_id))
                self._set_status(c, [posting_id], version, "failed", run_id=job_id, error=error)
            pending = c.scalar(
                select(func.count())
                .select_from(job_items)
                .where(job_items.c.job_id == job_id, job_items.c.status == "pending")
            )
            if pending == 0:
                c.execute(
                    update(jobs)
                    .where(jobs.c.id == job_id, jobs.c.status != "done")
                    .values(status="done", finished_at=now())
                )
            else:
                c.execute(update(jobs).where(jobs.c.id == job_id, jobs.c.status == "queued").values(status="running"))
        return True

    def open_jobs_for(self, version: str) -> list[int]:
        with self.tx() as c:
            return list(
                c.scalars(
                    select(jobs.c.id).where(jobs.c.policy_version == version, jobs.c.status.in_(("queued", "running")))
                )
            )

    def close_finished_jobs(self) -> list[int]:
        """Close jobs left "running" with no pending item (written before job rows were locked
        in mark_item). Workers call it on start; idempotent."""
        with self.tx() as c:
            pending = select(job_items.c.job_id).where(job_items.c.status == "pending")
            ids = list(
                c.scalars(select(jobs.c.id).where(jobs.c.status.in_(("queued", "running")), jobs.c.id.not_in(pending)))
            )
            if ids:
                c.execute(update(jobs).where(jobs.c.id.in_(ids)).values(status="done", finished_at=now()))
            return ids

    def cancel_job(self, job_id: int) -> int:
        """Stop a queued or running job: its pending items are cancelled (workers skip their
        messages), and the postings' status goes back to what their stored label says."""
        with self.tx() as c:
            job = c.execute(
                select(jobs.c.status, jobs.c.policy_version).where(jobs.c.id == job_id).with_for_update()
            ).first()
            if job is None or job.status not in ("queued", "running"):
                return 0
            ids = list(
                c.scalars(
                    select(job_items.c.posting_id).where(job_items.c.job_id == job_id, job_items.c.status == "pending")
                )
            )
            c.execute(
                update(job_items)
                .where(job_items.c.job_id == job_id, job_items.c.status == "pending")
                .values(status="cancelled")
            )
            c.execute(
                update(jobs)
                .where(jobs.c.id == job_id)
                .values(status="cancelled", finished_at=now(), detail=jobs.c.detail + " (cancelled)")
            )
            c.execute(
                delete(label_status).where(
                    label_status.c.run_id == job_id,
                    label_status.c.status == "queued",
                    label_status.c.policy_version == job.policy_version,
                )
            )
            for i in range(0, len(ids), 1000):  # restore done/failed from labels that already exist
                chunk = ids[i : i + 1000]
                rows = c.execute(
                    select(labels.c.job_id, labels.c.tier).where(
                        labels.c.policy_version == job.policy_version, labels.c.job_id.in_(chunk)
                    )
                ).all()
                for pid, tier in rows:
                    self._set_status(c, [pid], job.policy_version, "failed" if tier == "failed" else "done")
        return len(ids)

    def pending_items(self, job_id: int) -> list[str]:
        with self.tx() as c:
            return list(
                c.scalars(
                    select(job_items.c.posting_id).where(job_items.c.job_id == job_id, job_items.c.status == "pending")
                )
            )

    def claim_item(self, job_id: int, posting_id: str, lease_s: float) -> bool:
        """Take a pending item for `lease_s` seconds. One atomic UPDATE, so of two workers that got
        the same message (redelivery while the first is still labeling) exactly one wins."""
        t = datetime.now(UTC)
        stamp, until = t.isoformat(timespec="seconds"), (t + timedelta(seconds=lease_s)).isoformat(timespec="seconds")
        with self.tx() as c:
            res = c.execute(
                update(job_items)
                .where(
                    job_items.c.job_id == job_id,
                    job_items.c.posting_id == posting_id,
                    job_items.c.status == "pending",
                    or_(job_items.c.claimed_until.is_(None), job_items.c.claimed_until < stamp),
                )
                .values(claimed_until=until)
            )
            return res.rowcount == 1

    def release_item(self, job_id: int, posting_id: str) -> None:
        """Give a claimed item back after a failed attempt, so the next delivery of its message (after
        the visibility timeout, or a duplicate SQS delivery) can claim it without waiting for the lease."""
        with self.tx() as c:
            c.execute(
                update(job_items)
                .where(job_items.c.job_id == job_id, job_items.c.posting_id == posting_id)
                .values(claimed_until=None)
            )

    def item_pending(self, job_id: int, posting_id: str) -> bool:
        with self.tx() as c:
            st = c.scalar(
                select(job_items.c.status).where(job_items.c.job_id == job_id, job_items.c.posting_id == posting_id)
            )
        return st == "pending"

    def fail_job(self, job_id: int, detail: str) -> None:
        with self.tx() as c:
            c.execute(update(jobs).where(jobs.c.id == job_id).values(status="failed", detail=detail, finished_at=now()))

    def _job_select(self):
        done = func.sum(case((job_items.c.status == "done", 1), else_=0))
        failed = func.sum(case((job_items.c.status == "failed", 1), else_=0))
        counts = (
            select(job_items.c.job_id, done.label("done"), failed.label("failed"))
            .group_by(job_items.c.job_id)
            .subquery()
        )
        return select(
            jobs, func.coalesce(counts.c.done, 0).label("done"), func.coalesce(counts.c.failed, 0).label("failed")
        ).select_from(jobs.outerjoin(counts, counts.c.job_id == jobs.c.id))

    def get_job(self, job_id: int) -> dict[str, Any] | None:
        with self.tx() as c:
            r = c.execute(self._job_select().where(jobs.c.id == job_id)).mappings().first()
        return dict(r) if r else None

    def list_jobs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.tx() as c:
            return [dict(r) for r in c.execute(self._job_select().order_by(jobs.c.id.desc()).limit(limit)).mappings()]

    # ---- run-time settings -----------------------------------------------
    def get_app_settings(self) -> dict[str, Any]:
        with self.tx() as c:
            return {r.key: json.loads(r.value) for r in c.execute(select(app_settings))}

    def set_app_settings(self, values: dict[str, Any]) -> None:
        """Upsert the given keys; a value of None removes the override (back to the env default)."""
        with self.tx() as c:
            for key, value in values.items():
                c.execute(delete(app_settings).where(app_settings.c.key == key))
                if value is not None:
                    c.execute(insert(app_settings).values(key=key, value=json.dumps(value), updated_at=now()))

    def clear_app_settings(self) -> None:
        with self.tx() as c:
            c.execute(delete(app_settings))

    # ---- workers ----------------------------------------------------------
    def worker_heartbeat(
        self,
        worker_id: str,
        *,
        role: str,
        concurrency: int,
        inflight: int,
        processed: int,
        failed: int,
        status: str = "running",
        detail: str = "",
    ) -> None:
        t = now()
        values = {
            "role": role,
            "concurrency": concurrency,
            "inflight": inflight,
            "processed": processed,
            "failed": failed,
            "status": status,
            "detail": detail,
        }
        with self.tx() as c:
            res = c.execute(update(workers).where(workers.c.worker_id == worker_id).values(last_seen=t, **values))
            if res.rowcount == 0:
                c.execute(insert(workers).values(worker_id=worker_id, started_at=t, last_seen=t, **values))

    def worker_gone(self, worker_id: str) -> None:
        with self.tx() as c:
            c.execute(delete(workers).where(workers.c.worker_id == worker_id))

    def live_workers(self, max_age_s: float = 30.0) -> list[dict[str, Any]]:
        cutoff = (datetime.now(UTC) - timedelta(seconds=max_age_s)).isoformat(timespec="seconds")
        with self.tx() as c:
            c.execute(delete(workers).where(workers.c.last_seen < cutoff))  # dead ones: crashed or killed
            return [dict(r) for r in c.execute(select(workers).order_by(workers.c.started_at)).mappings()]

    # ---- per-version label status --------------------------------------------
    def _set_status(
        self,
        c: Connection,
        posting_ids: list[str],
        version: str,
        status: str,
        *,
        run_id: int | None = None,
        error: str = "",
    ) -> None:
        if not posting_ids:
            return
        t = now()
        for i in range(0, len(posting_ids), 1000):
            rows = [
                {
                    "posting_id": p,
                    "policy_version": version,
                    "status": status,
                    "run_id": run_id,
                    "error": error,
                    "updated_at": t,
                }
                for p in posting_ids[i : i + 1000]
            ]
            stmt = self._insert(label_status).values(rows)
            set_ = {
                "status": stmt.excluded.status,
                "error": stmt.excluded.error,
                "updated_at": stmt.excluded.updated_at,
            }
            if run_id is not None:
                set_["run_id"] = stmt.excluded.run_id
            c.execute(
                stmt.on_conflict_do_update(
                    index_elements=[label_status.c.posting_id, label_status.c.policy_version], set_=set_
                )
            )

    def mark_labeling(self, posting_id: str, version: str, run_id: int) -> None:
        with self.tx() as c:
            self._set_status(c, [posting_id], version, "labeling", run_id=run_id)

    def statuses_of(self, posting_id: str) -> dict[str, dict[str, Any]]:
        """Version -> {status, run_id, error, updated_at} for one posting."""
        with self.tx() as c:
            rows = c.execute(select(label_status).where(label_status.c.posting_id == posting_id)).mappings()
            return {r["policy_version"]: {k: r[k] for k in ("status", "run_id", "error", "updated_at")} for r in rows}

    def statuses_for(self, posting_ids: list[str], version: str) -> dict[str, dict[str, Any]]:
        if not posting_ids:
            return {}
        with self.tx() as c:
            rows = c.execute(
                select(label_status).where(
                    label_status.c.policy_version == version, label_status.c.posting_id.in_(posting_ids)
                )
            ).mappings()
            return {r["posting_id"]: {k: r[k] for k in ("status", "run_id", "error", "updated_at")} for r in rows}

    WORK_STATES = ("queued", "labeling", "failed")

    def query_work(
        self, statuses: Iterable[str], version: str | None = None, *, limit: int = 50, offset: int = 0
    ) -> tuple[dict[str, dict[str, int]], int, list[dict[str, Any]]]:
        """Postings waiting, being labeled or failed: the Work tab. Counts are per version for
        every state; the page lists the requested states, those being labeled first."""
        wanted = [s for s in statuses if s in self.WORK_STATES] or list(self.WORK_STATES)
        with self.tx() as c:
            counts: dict[str, dict[str, int]] = {}
            for v, st, n in c.execute(
                select(label_status.c.policy_version, label_status.c.status, func.count())
                .where(label_status.c.status.in_(self.WORK_STATES))
                .group_by(label_status.c.policy_version, label_status.c.status)
            ).all():
                counts.setdefault(v, {})[st] = n
            conds = [label_status.c.status.in_(wanted)]
            if version:
                conds.append(label_status.c.policy_version == version)
            total = c.scalar(select(func.count()).select_from(label_status).where(*conds)) or 0
            order = case((label_status.c.status == "labeling", 0), (label_status.c.status == "queued", 1), else_=2)
            rows = c.execute(
                select(
                    label_status.c.posting_id,
                    label_status.c.policy_version,
                    label_status.c.status,
                    label_status.c.run_id,
                    label_status.c.error,
                    label_status.c.updated_at,
                    postings.c.title,
                    postings.c.location,
                )
                .select_from(label_status.outerjoin(postings, postings.c.job_id == label_status.c.posting_id))
                .where(*conds)
                .order_by(order, label_status.c.updated_at.desc(), label_status.c.posting_id)
                .limit(limit)
                .offset(offset)
            ).mappings()
            return counts, total, [dict(r) for r in rows]

    def status_counts(self, version: str) -> dict[str, int]:
        with self.tx() as c:
            rows = c.execute(
                select(label_status.c.status, func.count())
                .where(label_status.c.policy_version == version)
                .group_by(label_status.c.status)
            ).all()
        return {s: int(n) for s, n in rows}

    # ---- encrypted secrets (local secret store) ------------------------------
    def get_secret_row(self, name: str) -> dict[str, Any] | None:
        with self.tx() as c:
            r = c.execute(select(secrets).where(secrets.c.name == name)).mappings().first()
        return dict(r) if r else None

    def put_secret_row(self, name: str, ciphertext: str, hint: str) -> None:
        with self.tx() as c:
            c.execute(delete(secrets).where(secrets.c.name == name))
            c.execute(insert(secrets).values(name=name, ciphertext=ciphertext, hint=hint, updated_at=now()))

    def delete_secret_row(self, name: str) -> bool:
        with self.tx() as c:
            return c.execute(delete(secrets).where(secrets.c.name == name)).rowcount > 0

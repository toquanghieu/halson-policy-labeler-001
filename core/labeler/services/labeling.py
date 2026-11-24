"""Use cases shared by the API, the worker, the CLI scripts and the tests.

Producer side (API): store postings, create a job, enqueue one message per posting.
Consumer side (worker): `process_item` labels one posting and records the outcome.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from labeler.config import STORE_MARGIN_S
from labeler.errors import Conflict, InvalidInput, NotFound, PolicyError
from labeler.infra.db import DB
from labeler.infra.queue.port import QueueName, WorkQueue
from labeler.infra.telemetry import record_decision
from labeler.labeling.agent import LabelingAgent
from labeler.labeling.policy import Policy, parse_policy
from labeler.labeling.schemas import LabelResult, Posting
from labeler.services.settings import RuntimeConfig

log = logging.getLogger(__name__)


class ItemBusy(Exception):
    """Another worker holds this item's lease: leave the message on the queue, it will come back."""


class Service:
    def __init__(self, db: DB, agent: LabelingAgent, queue: WorkQueue, max_concurrency: int = 8):
        self.db = db
        self.agent = agent
        self.queue = queue
        self.max_concurrency = max_concurrency
        self.config = RuntimeConfig(db, agent.settings)
        self.registry: Any = None  # set by bootstrap (ProviderRegistry)
        self.secrets: Any = None  # set by bootstrap (SecretStore)

    def settings(self):
        """Effective settings: env defaults plus what the Settings page changed (cached a few seconds)."""
        return self.config.current()

    def blocked_reason(self) -> str | None:
        """Why this process cannot label right now (e.g. no API key for a tier's provider), or None."""
        if self.registry is None:
            return None
        s = self.settings()
        for tier in (1, 2):
            reason = self.registry.unavailable(s.provider_for(tier))
            if reason:
                return f"tier {tier} ({s.provider_for(tier)}): {reason}"
        return None

    # ---- policies -------------------------------------------------------
    def policy(self, version: str | None = None) -> Policy:
        row = self.db.get_policy_row(version) if version else self.db.published_policy_row()
        if not row:
            raise NotFound(f"policy {version or '(published)'} not found")
        return parse_policy(row["source"])

    def create_draft(self, source: str, note: str = "") -> Policy:
        policy = parse_policy(source)  # raises PolicyError with a readable message
        if self.db.get_policy_row(policy.version):
            raise Conflict(f"version {policy.version} already exists; pick a new version id")
        self.db.insert_policy(policy.version, source, "draft", note)
        return policy

    def update_draft(self, version: str, source: str, note: str = "") -> Policy:
        row = self.db.get_policy_row(version)
        if not row:
            raise NotFound(f"policy {version} not found")
        if row["status"] != "draft":
            raise Conflict("published and retired versions are immutable; create a new version instead")
        if open_jobs := self.db.open_jobs_for(version):
            # Workers read the draft per message: an edit now would split one run across two texts.
            ids = ", ".join(f"#{j}" for j in open_jobs)
            raise Conflict(f"{version} is being labeled (job {ids}); wait for it or cancel it before editing")
        policy = parse_policy(source)
        if policy.version != version:
            raise PolicyError(f"the document says version {policy.version} but you are editing {version}")
        self.db.update_policy_source(version, source, note)
        return policy

    async def publish(
        self, version: str, backfill: bool | None = None, days: int | None = None
    ) -> tuple[Policy, int | None, str | None]:
        """Publish a draft, or re-publish a retired version (rollback). New postings use it at once.

        Returns (policy, backfill job id, backfill error). Publishing is the commit point: if the
        backfill cannot be started afterwards (queue down), the version is still live and the
        error is reported next to it, so the caller can start the re-label again, instead of
        getting a failure for a publish that actually happened.

        Then, if `backfill` (default: the backfill_on_publish setting), re-label the last `days`
        (default: backfill_days) under it on the backfill queue. Postings that already have a label
        under this version are skipped: labels are keyed by (posting, version), so a rollback
        reuses the old version's labels and only labels what arrived since.
        """
        row = self.db.get_policy_row(version)
        if not row:
            raise NotFound(f"policy {version} not found")
        if row["status"] == "published":
            raise Conflict(f"{version} is already published")
        policy = parse_policy(row["source"])
        self.db.publish_policy(version)
        cfg = self.settings()
        if not (cfg.backfill_on_publish if backfill is None else backfill):
            return policy, None, None
        try:
            job = await self.start_relabel(version, "recent", days or cfg.backfill_days, skip_labeled=True)
        except Exception as e:
            log.exception("backfill after publishing %s could not start", version)
            return policy, None, f"{version} is published, but the backfill did not start: {e}"
        return policy, job, None

    def seed_policy_if_empty(self, source: str) -> None:
        if self.db.list_policies():
            return
        p = parse_policy(source)
        self.db.insert_policy(p.version, source, "published", "Initial policy from the assignment brief.")

    # ---- one posting, answered synchronously, labeled by a worker -------------
    async def label_and_wait(
        self, posting: Posting, version: str | None = None, wait_s: float | None = None
    ) -> tuple[LabelResult | None, int]:
        """POST /api/postings: the API never calls the model itself. The posting becomes a one-item
        job on the live queue, a worker labels it like any other (lease, retries, DLQ, the
        concurrency cap), and this waits for the decision. Returns (decision or None, job id):
        None when it is not done within `wait_s`, and the caller answers 202 with the job id.

        Waiting is a short database poll; at larger scale a notification (Postgres LISTEN/NOTIFY,
        or a reply queue) would replace it without changing the endpoint."""
        policy = self.policy(version)
        await asyncio.to_thread(self.db.upsert_postings, [(posting, None, False)])
        job_id = await self._start_job("single", policy, [posting.job_id], queue="sync")
        deadline = time.monotonic() + (wait_s if wait_s is not None else self.settings().sync_label_wait_s)
        pause = 0.05
        while time.monotonic() < deadline:
            job = await asyncio.to_thread(self.db.get_job, job_id)
            if job and job["status"] in ("done", "cancelled"):
                return await asyncio.to_thread(self.db.get_label, posting.job_id, policy.version), job_id
            await asyncio.sleep(pause)
            pause = min(pause * 2, 0.5)
        return None, job_id

    def labeling_unavailable(self) -> str | None:
        """Why a posting sent now would not be labeled, as the workers report it; None if it would.
        A live worker that is taking messages wins. Workers that are all waiting give their reason
        (e.g. no API key). With no heartbeat yet, this process's own view of the providers decides."""
        workers = self.db.live_workers()
        if any(w.get("status", "running") == "running" for w in workers):
            return None
        if workers:
            return workers[0].get("detail") or "workers are waiting"
        return self.blocked_reason()

    def _save(self, result: LabelResult) -> None:
        """Store a decision. Only a decision under the *published* version is news for the
        marketplace: shadow-runs of drafts, re-labels under retired versions and eval tags
        (v1@current) are stored for analysis and emit no PostingLabeled event."""
        published = (self.db.published_policy_row() or {}).get("version")
        self.db.save_label(result, emit_event=result.policy_version == published)

    def result_for(self, job_id: str, version: str | None = None) -> tuple[LabelResult | None, str]:
        """The label under `version`, defaulting to the published one."""
        v = version or self.policy().version
        return self.db.get_label(job_id, v), v

    # ---- producer: jobs onto the queue --------------------------------------
    async def submit_batch(self, postings: list[Posting], version: str | None = None) -> int:
        """New postings go on the live queue, enqueued before returning: an accepted batch is never lost."""
        policy = self.policy(version)
        await asyncio.to_thread(self.db.upsert_postings, [(p, None, False) for p in postings])
        return await self._start_job("batch", policy, [p.job_id for p in postings], queue="live")

    def relabel_ids(
        self,
        version: str,
        scope: str,
        days: int = 30,
        limit: int | None = None,
        ids: list[str] | None = None,
        skip_labeled: bool = False,
    ) -> list[str]:
        """Which postings a re-label would touch (also the UI's "this will re-label N postings").

        scope:
          eval      the fixed 400-posting evaluation set
          recent    postings with a decision made in the last `days` days (what Decisions shows)
          previous  every posting that has a label under the currently published version
          ids       the given posting ids (e.g. one posting from the case view)
        """
        if scope == "eval":
            ids = self.db.posting_ids(eval_only=True)
        elif scope == "recent":
            # Postings that got a decision in the last `days` (under any real version): the ones in
            # Decisions. Never-labeled postings (dataset rows, Messages) are not re-labeled; they are
            # labeled from Messages when someone asks.
            since = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")
            ids = self.db.ids_decided_since(since)
        elif scope == "previous":
            published = self.db.published_policy_row()
            ids = self.db.ids_labeled_under(published["version"]) if published else []
        elif scope == "ids":
            if not ids:
                raise InvalidInput("scope=ids needs a non-empty `ids` list")
            found = self.db.existing_ids(ids)  # one query per 1,000 ids, not one per id
            missing = [i for i in ids if i not in found]
            if missing:
                raise NotFound(f"postings not found: {', '.join(missing[:5])}")
        else:
            raise InvalidInput(f"unknown scope {scope!r}")
        if skip_labeled:
            have = set(self.db.ids_labeled_under(version))
            ids = [i for i in ids if i not in have]
        if limit:
            ids = ids[:limit]
        return ids

    async def start_relabel(
        self,
        version: str,
        scope: str,
        days: int = 30,
        limit: int | None = None,
        ids: list[str] | None = None,
        skip_labeled: bool = False,
    ) -> int:
        """Re-label existing postings under `version` (a draft can be shadow-run too), on the backfill queue."""
        policy = self.policy(version)
        todo = await asyncio.to_thread(self.relabel_ids, version, scope, days, limit, ids, skip_labeled)
        detail = f"scope={scope}" + (f" days={days}" if scope == "recent" else "")
        return await self._start_job("relabel", policy, todo, queue="backfill", detail=detail)

    async def _start_job(self, kind: str, policy: Policy, ids: list[str], *, queue: QueueName, detail: str = "") -> int:
        ids = list(dict.fromkeys(ids))
        job_id = await asyncio.to_thread(self.db.create_job, kind, policy.version, ids, queue=queue, detail=detail)
        bodies = [{"job_id": job_id, "posting_id": p, "policy_version": policy.version} for p in ids]
        try:
            await self.queue.send(queue, bodies)
        except Exception as e:
            await asyncio.to_thread(self.db.fail_job, job_id, f"enqueue failed: {e}")
            raise
        return job_id

    # ---- inbox (Messages tab) -------------------------------------------------
    def import_postings(self, postings: list[Posting]) -> dict[str, int]:
        """Store postings without labeling them; they wait in the Messages tab."""
        ids = [p.job_id for p in postings]
        if len(set(ids)) != len(ids):
            raise InvalidInput("duplicate job_id in the upload")
        existing = self.db.existing_ids(ids)
        self.db.upsert_postings([(p, None, False) for p in postings])
        return {"stored": len(ids), "new": len(ids) - len(existing), "updated": len(existing)}

    async def label_ids(self, ids: list[str], version: str | None = None) -> int:
        """Label stored postings now, on the live queue (the Messages tab's "Label" button)."""
        policy = self.policy(version)
        ids = list(dict.fromkeys(ids))
        if not ids:
            raise InvalidInput("nothing to label")
        missing = set(ids) - await asyncio.to_thread(self.db.existing_ids, ids)
        if missing:
            raise NotFound(f"postings not found: {', '.join(sorted(missing)[:5])}")
        return await self._start_job("batch", policy, ids, queue="live", detail="from Messages")

    async def requeue(self, job_id: int) -> int:
        """Send a job's still-pending items again (e.g. the local queue lost its messages on a
        restart). Safe to repeat: a worker skips items that are no longer pending."""
        job = await asyncio.to_thread(self.db.get_job, job_id)
        if not job:
            raise NotFound(f"job {job_id} not found")
        if job["status"] not in ("queued", "running"):
            raise Conflict(f"job is {job['status']}; nothing to re-send")
        ids = await asyncio.to_thread(self.db.pending_items, job_id)
        bodies = [{"job_id": job_id, "posting_id": p, "policy_version": job["policy_version"]} for p in ids]
        await self.queue.send(job["queue"], bodies)
        return len(ids)

    # ---- consumer: one queue message ----------------------------------------
    async def process_item(self, job_id: int, posting_id: str, version: str) -> bool:
        """Label one posting for a job and record it. Returns True if the item failed.

        Raises only on infrastructure errors (DB, object store), so the queue redelivers.
        A model failure is not an exception: the agent returns NEEDS_REVIEW, tier=failed.
        """
        found = await asyncio.to_thread(self.db.get_posting, posting_id)
        if not found:
            await asyncio.to_thread(self.db.mark_item, job_id, posting_id, failed=True, error="posting not found")
            return True
        # The lease covers the labeling deadline plus the time to store the result, which config keeps
        # below the visibility timeout: after a crash the lease has always run out by the time the
        # queue hands the message out again, so that delivery is a real retry, not an ItemBusy.
        cfg = self.settings()
        lease = cfg.label_deadline_s + STORE_MARGIN_S
        if not await asyncio.to_thread(self.db.claim_item, job_id, posting_id, lease):
            if await asyncio.to_thread(self.db.item_pending, job_id, posting_id):
                raise ItemBusy(f"job {job_id} posting {posting_id} is being labeled by another worker")
            return False  # cancelled, or already recorded by an earlier delivery: nothing to do, ack it
        try:
            await asyncio.to_thread(self.db.mark_labeling, posting_id, version, job_id)
            policy = self.policy(version)  # read per message: drafts can be edited between shadow runs
            result = await self.agent.label(found[0], policy, self.settings())
            await asyncio.to_thread(self._save, result)
        except BaseException:
            # The message is not acknowledged, so the queue hands it out again after the visibility
            # timeout. Releasing the lease means that delivery can claim the item straight away.
            await asyncio.to_thread(self.db.release_item, job_id, posting_id)
            raise
        record_decision(result)
        failed = result.trace.get("tier") == "failed"
        # The label is stored; a lost item update now would mean labeling (and paying for) it again
        # once the lease runs out, so a brief database hiccup is retried here.
        for attempt in range(3):
            try:
                await asyncio.to_thread(self.db.mark_item, job_id, posting_id, failed=failed)
                break
            except Exception:
                if attempt == 2:
                    raise
                await asyncio.sleep(0.5 * (attempt + 1))
        return failed

    # ---- direct, in-process runs (eval and demo scripts) ---------------------
    async def run_ids(self, policy: Policy, ids: list[str], skip_existing: bool = False) -> None:
        """Label `ids` in this process without the queue: offline evaluation runs."""
        sem = asyncio.Semaphore(self.max_concurrency)

        async def one(jid: str) -> None:
            async with sem:
                existing = self.db.get_label(jid, policy.version, full_trace=False) if skip_existing else None
                if existing and existing.trace.get("tier") != "failed":
                    return
                found = self.db.get_posting(jid)
                if not found:
                    return
                try:
                    result = await self.agent.label(found[0], policy)  # scripts pass their settings explicitly
                    await asyncio.to_thread(self._save, result)
                except Exception:
                    log.exception("labeling %s failed", jid)

        await asyncio.gather(*(one(j) for j in ids))

    # ---- comparison -----------------------------------------------------
    def posting_versions(self, job_id: str, versions: list[str]) -> list[dict]:
        """One posting's full decision under each requested version (None where it was never labeled)."""
        if not self.db.get_posting(job_id):
            raise NotFound(f"posting {job_id} not found")
        rows = {r["version"]: r for r in self.db.list_policies()}
        states = self.db.statuses_of(job_id)
        labeled_at = {h["policy_version"]: h["created_at"] for h in self.db.label_versions(job_id)}
        out = []
        for v in dict.fromkeys(versions):
            if v not in rows:
                raise NotFound(f"policy {v} not found")
            r = self.db.get_label(job_id, v)
            out.append(
                {
                    "policy_version": v,
                    "status": rows[v]["status"],
                    "result": r.model_dump(mode="json") if r else None,
                    # when this version's decision was made (UTC ISO; a re-label replaces it), or None
                    "labeled_at": labeled_at.get(v) if r else None,
                    # queued | labeling | done | failed (+ run_id, error), or None if never requested
                    "state": states.get(v),
                }
            )
        return out

    def compare(self, a: str, b: str) -> dict:
        """Which postings changed label between two policy versions."""
        rows_a = {r["job_id"]: r for r in self.db.label_rows(a)}
        rows_b = {r["job_id"]: r for r in self.db.label_rows(b)}
        common = sorted(set(rows_a) & set(rows_b), key=lambda x: (len(x), x))
        transitions: dict[str, int] = {}
        changed = []
        for jid in common:
            la, lb = rows_a[jid]["label"], rows_b[jid]["label"]
            key = f"{la}->{lb}"
            transitions[key] = transitions.get(key, 0) + 1
            if la != lb or rows_a[jid]["sections"] != rows_b[jid]["sections"]:
                changed.append(
                    {
                        "job_id": jid,
                        "from": {"label": la, "sections": rows_a[jid]["sections"]},
                        "to": {"label": lb, "sections": rows_b[jid]["sections"]},
                        "gold": rows_b[jid]["gold"],
                    }
                )
        return {"from": a, "to": b, "compared": len(common), "transitions": transitions, "changed": changed}

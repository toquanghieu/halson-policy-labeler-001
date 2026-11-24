"""Queue semantics (in-memory and SQS through moto) and the worker's scheduling and failure handling."""

import asyncio
import json

import boto3
import pytest
from moto import mock_aws

from labeler.bootstrap import build_service
from labeler.infra.llm.fake_adapter import FakeProvider
from labeler.infra.queue.memory_adapter import MemoryQueue
from labeler.infra.queue.sqs_adapter import SQSQueue
from labeler.labeling.schemas import Posting
from labeler_worker import MAX_RECEIVES, Worker


def run(coro):
    return asyncio.run(coro)


# ---- MemoryQueue: SQS semantics in miniature -----------------------------------------
def test_memory_queue_redelivers_unacked_after_visibility_timeout_then_dead_letters():
    async def go():
        q = MemoryQueue(visibility_timeout_s=0.01, max_receives=2)
        await q.send("live", [{"n": 1}])
        first = await q.receive("live", 10, 0)
        assert [m.body for m in first] == [{"n": 1}] and await q.receive("live", 10, 0) == []  # invisible
        await asyncio.sleep(0.02)
        second = await q.receive("live", 10, 0)
        assert second[0].receive_count == 2
        await asyncio.sleep(0.02)
        assert await q.receive("live", 10, 0) == [] and q.dlq["live"] == [{"n": 1}]

    run(go())


def test_memory_queue_ack_removes_and_long_poll_wakes_on_send():
    async def go():
        q = MemoryQueue()
        waiter = asyncio.create_task(q.receive("live", 1, 5))
        await asyncio.sleep(0.01)
        await q.send("live", [{"n": 2}])
        (msg,) = await asyncio.wait_for(waiter, 1)
        await q.ack("live", msg)
        assert await q.depth("live") == {"visible": 0, "in_flight": 0}

    run(go())


# ---- SQSQueue against moto's SQS -----------------------------------------------------
@mock_aws
def test_sqs_queue_creates_redrive_to_dlq_and_round_trips():
    q = SQSQueue({"live": "t-live", "backfill": "t-backfill"}, endpoint_url=None, region="us-east-1")
    q.ensure()
    sqs = boto3.client("sqs", region_name="us-east-1")
    attrs = sqs.get_queue_attributes(QueueUrl=q._url("live"), AttributeNames=["RedrivePolicy"])["Attributes"]
    assert json.loads(attrs["RedrivePolicy"])["deadLetterTargetArn"].endswith("t-live-dlq")
    q.ensure()  # idempotent: existing queues are reused

    async def go():
        await q.send("live", [{"i": i} for i in range(23)])  # > one SendMessageBatch of 10
        got = []
        while len(got) < 23:
            got += await q.receive("live", 10, 0)
        for m in got:
            await q.ack("live", m)
        assert sorted(m.body["i"] for m in got) == list(range(23))
        assert (await q.depth("live"))["visible"] == 0

    run(go())


# ---- Worker ---------------------------------------------------------------------------
@pytest.fixture
def svc_and_queue(settings):
    queue = MemoryQueue(visibility_timeout_s=0.05)
    svc = build_service(settings, FakeProvider(), queue=queue)
    svc.db.upsert_postings([(Posting(job_id=str(i), title=f"t{i}"), None, False) for i in range(6)])
    return svc, queue


def test_worker_drains_live_before_backfill(svc_and_queue):
    svc, queue = svc_and_queue
    order: list[str] = []
    real = svc.process_item

    async def spy(job_id, posting_id, version):
        order.append(posting_id)
        return await real(job_id, posting_id, version)

    svc.process_item = spy

    async def go():
        backfill = await svc.start_relabel("v1", "eval")  # no eval postings: empty job
        assert svc.db.get_job(backfill)["status"] == "done"
        await queue.send("backfill", [{"job_id": 0, "posting_id": p, "policy_version": "v1"} for p in ("4", "5")])
        live = await svc.submit_batch([svc.db.get_posting(p)[0] for p in ("0", "1", "2")])
        worker = Worker(svc, queue, concurrency=1, idle_wait_s=0.01)
        task = asyncio.create_task(worker.run())
        for _ in range(200):
            if len(order) >= 5:
                break
            await asyncio.sleep(0.01)
        worker.stop()
        await task
        assert order[:3] == ["0", "1", "2"] and sorted(order[3:]) == ["4", "5"]
        assert svc.db.get_job(live)["status"] == "done"

    run(go())


def test_worker_leaves_failing_message_for_redelivery_and_closes_item_on_last_try(svc_and_queue):
    svc, queue = svc_and_queue
    attempts = []

    async def broken(job_id, posting_id, version):
        attempts.append(posting_id)
        raise ConnectionError("database unavailable")

    svc.process_item = broken

    async def go():
        job = await svc.submit_batch([svc.db.get_posting("0")[0]])
        worker = Worker(svc, queue, concurrency=2, idle_wait_s=0.01)
        task = asyncio.create_task(worker.run())
        for _ in range(300):
            if svc.db.get_job(job)["status"] == "done":
                break
            await asyncio.sleep(0.01)
        worker.stop()
        await task
        j = svc.db.get_job(job)
        assert len(attempts) == MAX_RECEIVES  # retried by redelivery, not in a tight loop
        assert (j["status"], j["failed"]) == ("done", 1)  # the job still completes; the message is in the DLQ

    run(go())


def test_api_batch_flows_through_queue_and_worker(svc_and_queue):
    svc, queue = svc_and_queue

    async def go():
        job = await svc.submit_batch([svc.db.get_posting(p)[0] for p in ("0", "1")])
        assert (await queue.depth("live"))["visible"] == 2  # enqueued before returning
        worker = Worker(svc, queue, concurrency=4, idle_wait_s=0.01)
        task = asyncio.create_task(worker.run())
        for _ in range(200):
            if svc.db.get_job(job)["status"] == "done":
                break
            await asyncio.sleep(0.01)
        worker.stop()
        await task
        assert svc.db.get_label("0", "v1") is not None and len(svc.db.events_after(0)) == 2

    run(go())


def test_worker_survives_queue_outage_and_resumes(svc_and_queue):
    svc, queue = svc_and_queue
    real_receive = queue.receive
    outages = {"left": 3}

    async def flaky(name, n, wait_s):
        if outages["left"]:
            outages["left"] -= 1
            raise ConnectionError("queue restarting")
        return await real_receive(name, n, wait_s)

    queue.receive = flaky

    async def go():
        job = await svc.submit_batch([svc.db.get_posting("3")[0]])
        worker = Worker(svc, queue, concurrency=2, idle_wait_s=0.01, max_backoff_s=0.02)
        task = asyncio.create_task(worker.run())
        for _ in range(300):
            if svc.db.get_job(job)["status"] == "done":
                break
            await asyncio.sleep(0.01)
        worker.stop()
        await task
        assert outages["left"] == 0 and svc.db.get_job(job)["status"] == "done"

    run(go())


def test_a_redelivered_message_is_not_labeled_twice(settings):
    """The message reappears (visibility 0.05 s) while the first worker is still labeling (0.3 s):
    the lease keeps the second delivery from calling the model and emitting a second event."""
    from labeler.infra.llm.port import LLMResponse, Usage

    calls: list[str] = []

    class Slow:
        name = "slow"

        async def complete(self, req):
            calls.append(req.user)
            await asyncio.sleep(0.3)
            data = {"findings": [], "insufficient_evidence": False, "rationale": "ok", "confidence": 0.9}
            return LLMResponse(data=data, model=req.model, usage=Usage(), latency_ms=300.0)

    queue = MemoryQueue(visibility_timeout_s=0.05)
    s = settings.__class__(**{**settings.__dict__, "tier2_model": ""})
    svc = build_service(s, Slow(), queue=queue)
    svc.db.upsert_postings([(Posting(job_id=str(i), title=f"t{i}"), None, False) for i in range(2)])

    async def go():
        job = await svc.submit_batch([svc.db.get_posting(p)[0] for p in ("0", "1")])
        worker = Worker(svc, queue, concurrency=4, idle_wait_s=0.01)
        task = asyncio.create_task(worker.run())
        for _ in range(300):
            if svc.db.get_job(job)["status"] == "done":
                break
            await asyncio.sleep(0.01)
        worker.stop()
        await task
        assert svc.db.get_job(job)["status"] == "done"
        assert len(calls) == 2  # one model call per posting, despite the redeliveries
        assert len(svc.db.events_after(0)) == 2

    run(go())


def test_a_waiting_caller_goes_ahead_of_a_queued_batch(svc_and_queue):
    """One worker slot, a 4-posting batch on `live`, then one posting on `sync`: the sync one is
    labeled right after the posting already in flight, not after the whole batch."""
    svc, queue = svc_and_queue
    order: list[str] = []
    real = svc.process_item

    async def spy(job_id, posting_id, version):
        order.append(posting_id)
        return await real(job_id, posting_id, version)

    svc.process_item = spy

    async def go():
        await svc.submit_batch([svc.db.get_posting(p)[0] for p in ("0", "1", "2", "3")])
        policy = svc.policy()
        await svc._start_job("single", policy, ["5"], queue="sync")
        worker = Worker(svc, queue, concurrency=1, idle_wait_s=0.01)
        task = asyncio.create_task(worker.run())
        for _ in range(300):
            if len(order) >= 5:
                break
            await asyncio.sleep(0.01)
        worker.stop()
        await task
        assert order[0] == "5"  # sync first, before any of the batch

    run(go())


def test_the_lease_always_ends_before_the_message_comes_back(settings):
    from labeler.config import STORE_MARGIN_S

    lease = settings.label_deadline_s + STORE_MARGIN_S
    assert lease <= settings.queue_visibility_timeout_s  # a redelivery after a crash is a real retry

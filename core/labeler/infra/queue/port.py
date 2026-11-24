"""Port: the work queue between the API (producer) and the workers (consumers).

Work queue between the API (producer) and the labeling workers (consumers).

Two queues, as in the design: `live` for new postings (latency-bound) and `backfill` for
re-labels after a policy change (throughput-bound, never allowed to starve live traffic).
Each has a dead-letter queue: a message that fails 3 receives is parked there, not retried
forever.

Production: Amazon SQS. Local compose: ElasticMQ, which speaks the SQS API, so `SQSQueue`
is the production code path with a different endpoint. Tests and `make dev`: `MemoryQueue`.
At-least-once delivery either way; the worker is idempotent (see DB.mark_item).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

QueueName = Literal["sync", "live", "backfill"]
# Read in this order: `sync` (a caller is waiting on POST /api/postings), then `live` (new postings),
# then `backfill` (re-labels after a policy change), so bulk work never delays a waiting caller.
QUEUES: tuple[QueueName, ...] = ("sync", "live", "backfill")


@dataclass(frozen=True)
class Message:
    body: dict[str, Any]
    receipt: str
    receive_count: int = 1


class WorkQueue(Protocol):
    async def send(self, queue: QueueName, bodies: list[dict[str, Any]]) -> None: ...
    async def receive(self, queue: QueueName, max_messages: int, wait_s: float) -> list[Message]: ...
    async def ack(self, queue: QueueName, msg: Message) -> None: ...
    async def depth(self, queue: QueueName) -> dict[str, int]: ...

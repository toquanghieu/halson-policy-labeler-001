"""Adapter: an in-process queue for tests and `make dev`, with SQS semantics in miniature."""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from labeler.infra.queue.port import QUEUES, Message, QueueName


@dataclass
class _Entry:
    body: dict[str, Any]
    receives: int = 0
    invisible_until: float = 0.0


@dataclass
class MemoryQueue:
    """Same semantics as SQS in miniature: visibility timeout, redelivery, DLQ after N receives."""

    visibility_timeout_s: float = 300.0
    max_receives: int = 3
    _q: dict[str, dict[str, _Entry]] = field(default_factory=lambda: {q: {} for q in QUEUES})
    dlq: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: {q: [] for q in QUEUES})
    _cond: asyncio.Condition | None = None

    def _condition(self) -> asyncio.Condition:
        if self._cond is None:
            self._cond = asyncio.Condition()
        return self._cond

    async def send(self, queue: QueueName, bodies: list[dict[str, Any]]) -> None:
        for b in bodies:
            self._q[queue][uuid.uuid4().hex] = _Entry(body=b)
        async with self._condition():
            self._condition().notify_all()

    def _take(self, queue: QueueName, n: int) -> list[Message]:
        t = time.monotonic()
        out: list[Message] = []
        for receipt, e in list(self._q[queue].items()):
            if len(out) >= n:
                break
            if e.invisible_until > t:
                continue
            if e.receives >= self.max_receives:
                self.dlq[queue].append(e.body)
                del self._q[queue][receipt]
                continue
            e.receives += 1
            e.invisible_until = t + self.visibility_timeout_s
            out.append(Message(body=e.body, receipt=receipt, receive_count=e.receives))
        return out

    async def receive(self, queue: QueueName, max_messages: int, wait_s: float) -> list[Message]:
        msgs = self._take(queue, max_messages)
        if msgs or wait_s <= 0:
            return msgs
        cond = self._condition()
        async with cond:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(cond.wait(), timeout=wait_s)
        return self._take(queue, max_messages)

    async def ack(self, queue: QueueName, msg: Message) -> None:
        self._q[queue].pop(msg.receipt, None)

    async def depth(self, queue: QueueName) -> dict[str, int]:
        t = time.monotonic()
        entries = self._q[queue].values()
        return {
            "visible": sum(1 for e in entries if e.invisible_until <= t),
            "in_flight": sum(1 for e in entries if e.invisible_until > t),
        }

"""Worker liveness, stored in the database so every API replica sees every worker.

In AWS the same signal is the ECS service's running task count; here a row per worker,
refreshed every few seconds, is enough for the UI to warn "queued, but no worker is running".
"""

from __future__ import annotations

import os
import socket
import uuid

from labeler.infra.db import DB


class Heartbeat:
    def __init__(self, db: DB, *, role: str, concurrency: int):
        self.db = db
        self.worker_id = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        self.role = role
        self.concurrency = concurrency
        self.processed = 0
        self.failed = 0
        self.status = "running"
        self.detail = ""

    def count(self, failed: bool) -> None:
        self.processed += 1
        self.failed += int(failed)

    def beat(self, inflight: int) -> None:
        self.db.worker_heartbeat(
            self.worker_id,
            role=self.role,
            concurrency=self.concurrency,
            inflight=inflight,
            processed=self.processed,
            failed=self.failed,
            status=self.status,
            detail=self.detail,
        )

    def stopped(self) -> None:
        self.db.worker_gone(self.worker_id)

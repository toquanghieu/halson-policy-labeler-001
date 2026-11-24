"""Standalone worker process: `python -m labeler_worker` (compose `worker` service, `make worker`).

Needs a shared queue (QUEUE_BACKEND=sqs). Serves its own Prometheus metrics on METRICS_PORT and
drains in-flight postings on SIGTERM before exiting, so scaling down never loses work.
"""

from __future__ import annotations

import asyncio
import logging
import signal

from prometheus_client import start_http_server

from labeler.bootstrap import build_queue, build_service
from labeler.config import get_settings
from labeler_worker.consumer import Worker


async def main() -> None:
    settings = get_settings()
    if settings.queue_backend == "memory":
        raise SystemExit("a standalone worker needs a shared queue: set QUEUE_BACKEND=sqs")
    queue = build_queue(settings)
    svc = build_service(settings, queue=queue)
    start_http_server(settings.metrics_port)
    worker = Worker(svc, queue, settings.max_concurrency)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, worker.stop)
    try:
        await worker.run()
    finally:
        svc.db.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(main())

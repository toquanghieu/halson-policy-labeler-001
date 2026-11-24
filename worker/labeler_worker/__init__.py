"""The labeling worker: its own process in production (ECS service; compose `worker`).

    consumer.py   Worker: live before backfill, bounded concurrency, ack after store, DLQ path
    heartbeat.py  "I am alive" rows, so the API and UI can tell when no one consumes the queue
    __main__.py   standalone entry point: signals, metrics endpoint, graceful drain

The API process only enqueues (labeler_api); this package is the consumer side.
"""

from labeler_worker.consumer import MAX_RECEIVES, Worker

__all__ = ["MAX_RECEIVES", "Worker"]

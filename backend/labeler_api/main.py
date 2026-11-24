"""FastAPI application factory: JSON API under /api, Prometheus at /metrics, the analyst UI at /.

uvicorn labeler_api.main:app

This package is the HTTP layer only. Labeling, storage and queues live in `labeler` (core/);
the consumer lives in `labeler_worker` (worker/) and runs here only when EMBEDDED_WORKER=true.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from labeler.bootstrap import build_queue, build_service
from labeler.config import Settings, get_settings
from labeler.infra.llm.port import LLMProvider
from labeler_api.errors import register_exception_handlers
from labeler_api.routes import analyst, inbox, labeling, ops, policies
from labeler_api.routes import settings as settings_routes
from labeler_api.ui import mount_ui

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def create_app(settings: Settings | None = None, provider: LLMProvider | None = None) -> FastAPI:
    """Build the app. Tests pass their own settings and a scripted provider."""
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        queue = build_queue(settings)
        svc = build_service(settings, provider, queue=queue)
        app.state.settings, app.state.service, app.state.queue = settings, svc, queue
        worker_task = None
        if settings.embedded_worker:
            # One-process mode (`make dev`, tests): the worker package runs inside the API. The API
            # image does not ship it; there EMBEDDED_WORKER=false and worker/ is its own service.
            try:
                from labeler_worker import Worker
            except ImportError as e:
                raise RuntimeError(
                    "EMBEDDED_WORKER=true needs the worker package (worker/); install the workspace "
                    "with `uv sync`, or set EMBEDDED_WORKER=false and run the worker service"
                ) from e
            worker = Worker(svc, queue, settings.max_concurrency, idle_wait_s=0.5, role="embedded")
            app.state.worker = worker
            worker_task = asyncio.create_task(worker.run())
        yield
        if worker_task:
            worker.stop()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.wait_for(worker_task, timeout=30)
        svc.db.close()

    app = FastAPI(title="Policy Labeler", version="1.0", lifespan=lifespan)
    register_exception_handlers(app)
    for module in (labeling, inbox, analyst, policies, settings_routes, ops):
        app.include_router(module.router)

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    mount_ui(app)  # last: its catch-all route must not shadow /api or /metrics
    return app


app = create_app()

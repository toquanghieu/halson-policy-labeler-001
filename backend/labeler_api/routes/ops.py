"""Operational endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from labeler.infra.queue.port import QUEUES
from labeler_api.deps import ServiceDep, SettingsDep

router = APIRouter(prefix="/api", tags=["ops"])


@router.get("/health")
async def health(svc: ServiceDep, settings: SettingsDep, request: Request) -> dict[str, Any]:
    queues: dict[str, Any] = {}
    for q in QUEUES:
        try:
            queues[q] = await request.app.state.queue.depth(q)
        except Exception as e:  # noqa: BLE001  (health must answer even when the queue is down)
            queues[q] = {"error": str(e)}
    return {
        "ok": True,
        "provider": settings.llm_provider,
        "tier1": svc.settings().tier1_model,  # effective values: the Settings page can change them
        "tier2": svc.settings().tier2_model,
        "tier1_provider": svc.settings().provider_for(1),
        "tier2_provider": svc.settings().provider_for(2),
        "secrets_backend": svc.secrets.backend if svc.secrets else None,
        "router": svc.agent.router is not None,
        "published_policy": (svc.db.published_policy_row() or {}).get("version"),
        "database": svc.db.engine.dialect.name,
        "queue": settings.queue_backend,
        "storage": settings.storage_backend,
        "embedded_worker": settings.embedded_worker,
        "queues": queues,
        # Workers that sent a heartbeat in the last 30 s. 0 with a non-empty queue = nothing is consumed.
        "workers": svc.db.live_workers(),
    }

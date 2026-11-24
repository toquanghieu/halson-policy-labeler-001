"""Policy lifecycle: draft, validate, edit, publish, shadow-run / re-label."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from labeler.errors import PolicyError
from labeler.labeling.policy import parse_policy
from labeler_api.deps import ServiceDep
from labeler_api.schemas import PolicyIn, PublishIn, RelabelIn

router = APIRouter(prefix="/api/policies", tags=["policy"])


@router.get("")
def list_policies(svc: ServiceDep) -> list[dict[str, Any]]:
    return svc.db.list_policies()


@router.post("", status_code=201)
def create_policy(body: PolicyIn, svc: ServiceDep) -> dict[str, Any]:
    """Create a draft. The version id comes from the document itself."""
    p = svc.create_draft(body.source, body.note)
    return {"version": p.version, "status": "draft"}


@router.post("/validate")
def validate_policy(body: PolicyIn) -> dict[str, Any]:
    """Parse without saving; the editor calls this as the analyst types."""
    try:
        p = parse_policy(body.source)
    except PolicyError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "parsed": p.model_dump(mode="json"), "rendered": p.render()}


@router.get("/{version}")
def get_policy(version: str, svc: ServiceDep) -> dict[str, Any]:
    row = svc.db.get_policy_row(version)
    if not row:
        raise HTTPException(404, "policy not found")
    return {
        **row,
        "parsed": svc.policy(version).model_dump(mode="json"),
        "label_status": svc.db.status_counts(version),  # e.g. {"done": 400, "queued": 12, "failed": 1}
    }


@router.put("/{version}")
def update_policy(version: str, body: PolicyIn, svc: ServiceDep) -> dict[str, Any]:
    p = svc.update_draft(version, body.source, body.note)
    return {"version": p.version, "status": "draft"}


@router.delete("/{version}")
def delete_policy(version: str, svc: ServiceDep) -> dict[str, Any]:
    if not svc.db.delete_draft(version):
        raise HTTPException(409, "only drafts can be deleted")
    return {"deleted": version}


@router.post("/{version}/publish")
async def publish_policy(version: str, svc: ServiceDep, body: PublishIn | None = None) -> dict[str, Any]:
    """Publish a draft, or re-publish a retired version to roll back. Optionally starts the backfill."""
    body = body or PublishIn()
    p, job_id, backfill_error = await svc.publish(version, body.backfill, body.days)
    out: dict[str, Any] = {"version": p.version, "status": "published", "backfill_job_id": job_id}
    if backfill_error:
        out["backfill_error"] = backfill_error
    if job_id is not None:
        out["status_url"] = f"/api/jobs/{job_id}"
    return out


@router.get("/{version}/relabel/preview")
def relabel_preview(
    version: str,
    svc: ServiceDep,
    scope: str = "recent",
    days: int = Query(30, ge=1, le=365),
    skip_labeled: bool = True,
) -> dict[str, Any]:
    """How many postings a re-label would touch, before starting it (and paying for it)."""
    svc.policy(version)  # 404 for an unknown version
    ids = svc.relabel_ids(version, scope, days, skip_labeled=skip_labeled)
    everything = ids if not skip_labeled else svc.relabel_ids(version, scope, days)
    have = set(svc.db.ids_labeled_under(version))
    return {
        "version": version,
        "scope": scope,
        "days": days,
        "skip_labeled": skip_labeled,
        "count": len(ids),
        # Where the postings in scope stand: already decided under this version (what Decisions
        # shows for it), or decided only under other versions (they move to this one).
        "already_under_version": sum(1 for i in everything if i in have),
        "only_under_other_versions": sum(1 for i in everything if i not in have),
    }


@router.post("/{version}/relabel")
async def relabel(version: str, body: RelabelIn, svc: ServiceDep) -> dict[str, Any]:
    """Run a version over existing postings via the backfill queue. Works on drafts too (shadow run)."""
    job_id = await svc.start_relabel(version, body.scope, body.days, body.limit, body.ids, body.skip_labeled)
    return {"job_id": job_id, "status_url": f"/api/jobs/{job_id}"}

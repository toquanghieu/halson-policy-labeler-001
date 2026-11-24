"""Messages tab: postings waiting for a decision under the published policy.

Postings get here by import (no labeling) or because they were never labeled under the
current version (the brief's 400 eval postings, until labeled). Labeling one moves it to
Decisions: this list is "everything without a label under the published version".
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from labeler.errors import InvalidInput
from labeler.labeling.schemas import Posting
from labeler_api.deps import ServiceDep

router = APIRouter(prefix="/api/inbox", tags=["inbox"])

Scope = Literal["inbox", "eval", "imported", "all"]


class ImportIn(BaseModel):
    postings: list[Posting] = Field(min_length=1, max_length=5000)


class LabelIn(BaseModel):
    ids: list[str] | None = Field(default=None, max_length=5000, description="these postings")
    all_in_scope: bool = Field(default=False, description="or every unlabeled posting in `scope` (+ `q`), up to 5,000")
    scope: Scope = "inbox"
    q: str | None = None
    policy_version: str | None = None


@router.get("")
def list_inbox(
    svc: ServiceDep,
    scope: Scope = "inbox",
    q: str | None = None,
    policy_version: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """Unlabeled postings. `scope`: inbox (eval set + imported, default) | eval | imported | all."""
    version = policy_version or svc.policy().version
    total, counts, items = svc.db.query_unlabeled(version, scope=scope, q=q, limit=limit, offset=offset)
    return {"policy_version": version, "scope": scope, "total": total, "counts": counts, "items": items}


@router.post("/import", status_code=201)
def import_postings(body: ImportIn, svc: ServiceDep) -> dict[str, Any]:
    """Store postings without labeling them (up to 5,000). Existing ids are updated, keeping their
    dataset label and eval flag."""
    return svc.import_postings(body.postings)


@router.post("/label")
async def label(body: LabelIn, svc: ServiceDep) -> dict[str, Any]:
    """Put postings on the live queue under the published policy (or `policy_version`)."""
    version = body.policy_version or svc.policy().version
    if body.all_in_scope:
        ids = svc.db.unlabeled_ids(version, scope=body.scope, q=body.q)
    elif body.ids:
        ids = body.ids
    else:
        raise InvalidInput("give `ids` or set `all_in_scope`")
    job_id = await svc.label_ids(ids, version)
    return {"job_id": job_id, "count": len(ids), "policy_version": version, "status_url": f"/api/jobs/{job_id}"}

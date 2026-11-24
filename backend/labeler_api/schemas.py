"""Request bodies. Response shapes are the domain models in labeler.labeling.schemas."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from labeler.labeling.schemas import Posting


class BatchIn(BaseModel):
    postings: list[Posting] = Field(min_length=1, max_length=5000)
    policy_version: str | None = None


class PolicyIn(BaseModel):
    source: str
    note: str = ""


class RelabelIn(BaseModel):
    scope: Literal["eval", "recent", "previous", "ids"] = "eval"
    days: int = Field(default=30, ge=1, le=365)
    limit: int | None = Field(default=None, ge=1)
    ids: list[str] | None = Field(default=None, max_length=5000, description="for scope=ids")
    skip_labeled: bool = Field(default=False, description="skip postings already labeled under this version")


class PublishIn(BaseModel):
    backfill: bool | None = Field(
        default=None, description="re-label the last `days` after publishing; null = the backfill_on_publish setting"
    )
    days: int | None = Field(default=None, ge=1, le=365, description="null = the backfill_days setting")


class SettingsPatch(BaseModel):
    """Any subset of the run-time settings; see GET /api/settings for the keys."""

    values: dict[str, Any]

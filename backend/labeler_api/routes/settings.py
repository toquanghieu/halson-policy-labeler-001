"""Run-time settings: models per tier, escalation thresholds, backfill window (the Settings page)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from labeler.errors import InvalidInput, NotFound
from labeler.infra.llm.port import LLMError
from labeler.infra.llm.registry import CATALOG
from labeler.infra.secrets.port import KNOWN
from labeler_api.deps import ServiceDep
from labeler_api.schemas import SettingsPatch

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
def get_settings(svc: ServiceDep) -> dict[str, Any]:
    """Current values, the environment defaults, which keys are overridden, and the allowed choices."""
    return svc.config.describe()


@router.put("")
def update_settings(body: SettingsPatch, svc: ServiceDep) -> dict[str, Any]:
    """Change any subset. Takes effect on the API at once and on workers within a few seconds."""
    return svc.config.update(body.values)


@router.delete("")
def reset_settings(svc: ServiceDep) -> dict[str, Any]:
    """Drop every override: back to the environment defaults."""
    return svc.config.reset()


class SecretIn(BaseModel):
    value: str = Field(min_length=1, max_length=512)


class TestIn(BaseModel):
    model: str | None = None


@router.get("/providers")
def providers(svc: ServiceDep) -> dict[str, Any]:
    """Every model provider: what it is, its key status (never the key), base URL, model hints,
    and whether a free option exists. `secrets_backend` says where keys entered here are kept."""
    cur = svc.settings()
    out = []
    for name, meta in CATALOG.items():
        out.append(
            {
                "name": name,
                "label": meta["label"],
                "free": meta["free"],
                "models": meta["models"],
                "presets": meta.get("presets"),
                "base_url_setting": meta["base_url"],
                "base_url": getattr(cur, meta["base_url"]) if meta["base_url"] else None,
                "secret": svc.secrets.describe(meta["secret"]) if meta["secret"] else None,
                "used_by": [t for t in (1, 2) if cur.provider_for(t) == name],
            }
        )
    return {"secrets_backend": svc.secrets.backend, "providers": out}


@router.put("/secrets/{name}")
def put_secret(name: str, body: SecretIn, svc: ServiceDep) -> dict[str, Any]:
    """Store an API key in the configured secret store. Write-only: the response shows the source
    and last four characters, never the key."""
    if name not in KNOWN:
        raise NotFound(f"unknown secret {name}")
    svc.secrets.set(name, body.value)
    return svc.secrets.describe(name)


@router.delete("/secrets/{name}")
def delete_secret(name: str, svc: ServiceDep) -> dict[str, Any]:
    """Remove a stored key (an environment variable of the same name, if any, applies again)."""
    if name not in KNOWN:
        raise NotFound(f"unknown secret {name}")
    svc.secrets.delete(name)
    return svc.secrets.describe(name)


@router.post("/providers/{name}/test")
async def test_provider(name: str, body: TestIn, svc: ServiceDep) -> dict[str, Any]:
    """Check a provider with the current key and URL: list its models (where it can), then, if a
    model is given, make one tiny structured call (a few dozen tokens). Always 200; `ok` and
    `error` carry the outcome."""
    if name not in CATALOG:
        raise NotFound(f"unknown provider {name}")
    try:
        return await svc.registry.test(name, body.model)
    except LLMError as e:
        raise InvalidInput(str(e)) from e

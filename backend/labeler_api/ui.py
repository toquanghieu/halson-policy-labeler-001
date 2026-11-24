"""Serve the built analyst UI (frontend/dist) from the same origin as the API."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from labeler.config import REPO_ROOT


def mount_ui(app: FastAPI) -> None:
    dist = REPO_ROOT / "frontend" / "dist"
    if not dist.exists():
        return
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        # An unknown API or metrics path is a client error, not a page: answer 404 JSON, never the HTML shell.
        if path == "api" or path.startswith(("api/", "metrics")):
            raise HTTPException(status_code=404, detail=f"no such endpoint: /{path}")
        target = (dist / path).resolve()
        # Real files only, and never outside dist (no ../ traversal); everything else is the SPA shell.
        if path and target.is_file() and dist.resolve() in target.parents:
            return FileResponse(target)
        return FileResponse(dist / "index.html")

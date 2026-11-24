"""Map domain errors to HTTP responses in one place."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from labeler.errors import Conflict, DomainError, InvalidInput, NotFound, NotReady

_STATUS: list[tuple[type[DomainError], int]] = [(NotFound, 404), (Conflict, 409), (InvalidInput, 422), (NotReady, 503)]


async def _domain_error(_: Request, exc: Exception) -> JSONResponse:
    status = next((code for cls, code in _STATUS if isinstance(exc, cls)), 400)
    return JSONResponse(status_code=status, content={"detail": str(exc)})


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, _domain_error)

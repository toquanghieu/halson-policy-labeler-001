"""Adapter: environment variables / .env, read-only."""

from __future__ import annotations

import os
from typing import Any

from labeler.errors import InvalidInput
from labeler.infra.secrets.port import hint


class EnvSecrets:
    backend = "env"

    def get(self, name: str) -> str | None:
        return os.environ.get(name) or None

    def set(self, name: str, value: str) -> None:
        raise InvalidInput("SECRETS_BACKEND=env is read-only: put the key in .env or the environment")

    def delete(self, name: str) -> bool:
        raise InvalidInput("SECRETS_BACKEND=env is read-only")

    def describe(self, name: str) -> dict[str, Any]:
        v = self.get(name)
        return {"name": name, "set": bool(v), "source": "environment" if v else None, "hint": hint(v) if v else None}

"""Shared implementation for the adapters that call out (database, AWS): a short read cache."""

from __future__ import annotations

import time

CACHE_S = 5.0


class CachedSecrets:
    """Shared by the stores that call out: workers read a key per model call, so cache briefly."""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, str | None]] = {}

    def _cached(self, name: str, load) -> str | None:
        t = time.monotonic()
        hit = self._cache.get(name)
        if hit and t - hit[0] < CACHE_S:
            return hit[1]
        v = load()
        self._cache[name] = (t, v)
        return v

    def _forget(self, name: str) -> None:
        self._cache.pop(name, None)

"""Port: object storage.

Object storage for large, rarely read artefacts: full decision traces, eval reports, the router model.

Production: S3. Local compose: SeaweedFS (same API, `S3_ENDPOINT_URL`). Tests and no-infra dev:
a directory on disk. The database keeps only the key.
"""

from __future__ import annotations

import json
from typing import Any, Protocol


class ObjectStore(Protocol):
    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None: ...
    def get_bytes(self, key: str) -> bytes | None: ...


def put_json(store: ObjectStore, key: str, obj: Any) -> None:
    store.put_bytes(key, json.dumps(obj, ensure_ascii=False).encode(), "application/json")


def get_json(store: ObjectStore, key: str) -> Any | None:
    data = store.get_bytes(key)
    return json.loads(data) if data is not None else None

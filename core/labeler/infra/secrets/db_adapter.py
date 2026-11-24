"""Adapter: keys encrypted (Fernet) in the application database (local and compose)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from labeler.errors import InvalidInput
from labeler.infra.db import DB
from labeler.infra.secrets.cached import CachedSecrets
from labeler.infra.secrets.port import check_known, hint


class EncryptedDbSecrets(CachedSecrets):
    backend = "db"

    def __init__(self, db: DB, master_key: str = "", key_file: Path | None = None):
        super().__init__()
        from cryptography.fernet import Fernet

        self.db = db
        key = master_key.encode() if master_key else self._key_from_file(key_file)
        self.fernet = Fernet(key)

    @staticmethod
    def _key_from_file(path: Path | None) -> bytes:
        from cryptography.fernet import Fernet

        if path is None:
            raise InvalidInput("set SECRETS_KEY or give a key file")
        if path.exists():
            return path.read_bytes().strip()
        path.parent.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key()
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:  # another container created it first
            return path.read_bytes().strip()
        with os.fdopen(fd, "wb") as f:
            f.write(key)
        return key

    def get(self, name: str) -> str | None:
        def load() -> str | None:
            row = self.db.get_secret_row(name)
            if row:
                return self.fernet.decrypt(row["ciphertext"].encode()).decode()
            return os.environ.get(name) or None

        return self._cached(name, load)

    def set(self, name: str, value: str) -> None:
        check_known(name)
        value = value.strip()
        if not value:
            raise InvalidInput("empty key")
        self.db.put_secret_row(name, self.fernet.encrypt(value.encode()).decode(), hint(value))
        self._forget(name)

    def delete(self, name: str) -> bool:
        check_known(name)
        self._forget(name)
        return self.db.delete_secret_row(name)

    def describe(self, name: str) -> dict[str, Any]:
        row = self.db.get_secret_row(name)
        if row:
            return {"name": name, "set": True, "source": "settings (encrypted in the database)", "hint": row["hint"]}
        v = os.environ.get(name)
        return {"name": name, "set": bool(v), "source": "environment" if v else None, "hint": hint(v) if v else None}

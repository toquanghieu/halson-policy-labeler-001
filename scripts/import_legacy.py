"""Copy postings, policies and labels from the pre-Postgres SQLite file into the configured database.

    uv run python -m scripts.import_legacy [path/to/labeler.db]     # default: data/labeler.db

Idempotent (upserts). Full traces are written to the object store like any new label. Used once,
to carry the measured eval runs (RESULTS.md) over to the compose stack without re-spending on them.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from labeler.bootstrap import build_db
from labeler.config import get_settings
from labeler.labeling.schemas import LabelResult, Posting


def main() -> None:
    settings = get_settings()
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else settings.data_dir / "labeler.db"
    if not src.exists():
        raise SystemExit(f"no legacy database at {src}")
    old = sqlite3.connect(src)
    old.row_factory = sqlite3.Row
    db = build_db(settings)

    rows = old.execute("SELECT data, gold, is_eval FROM postings").fetchall()
    db.upsert_postings((Posting.model_validate_json(r["data"]), r["gold"], bool(r["is_eval"])) for r in rows)
    print(f"postings: {len(rows)}")

    n = 0
    for r in old.execute("SELECT * FROM policies ORDER BY created_at"):
        if db.get_policy_row(r["version"]):
            continue
        db.insert_policy(r["version"], r["source"], "draft", r["note"])
        n += 1
    for r in old.execute("SELECT version, status FROM policies WHERE status != 'draft' ORDER BY published_at"):
        db.publish_policy(r["version"])  # replays publish order; the last published one stays published
    published = old.execute("SELECT version FROM policies WHERE status='published'").fetchone()
    if published:
        db.publish_policy(published["version"])
    print(f"policies: {n} new; published {published['version'] if published else '-'}")

    labels = old.execute("SELECT result, created_at FROM labels").fetchall()
    for i, r in enumerate(labels, 1):
        db.save_label(LabelResult.model_validate_json(r["result"]), emit_event=False, created_at=r["created_at"])
        if i % 200 == 0:
            print(f"labels: {i}/{len(labels)}")
    print(f"labels: {len(labels)} (traces -> {settings.storage_backend} object store)")


if __name__ == "__main__":
    main()

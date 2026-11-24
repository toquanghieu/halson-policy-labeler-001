"""One-shot initialisation, idempotent: what the compose `init` service runs before API and workers.

    uv run python -m scripts.init

1. migrate the database (Alembic, to head)
2. load EMSCAD into `postings` if empty (downloads the CSV on first run)
3. train the router if the object store has none, and publish it there
4. seed policy v1 if there are no policies
5. if no labels exist yet: import data/labeler.db when present, else the shipped seed (RESULTS.md runs)
"""

from __future__ import annotations

from labeler.bootstrap import ROUTER_KEY, build_db, build_store
from labeler.config import get_settings
from labeler.labeling.policy import parse_policy

from . import data, import_legacy, seed


def main() -> None:
    settings = get_settings()
    store = build_store(settings)
    db = build_db(settings, store)  # runs migrations
    print(f"database {db.engine.dialect.name}: migrated")

    if db.count("postings") == 0:
        data.load()
    else:
        print(f"postings: {db.count('postings')} already loaded")

    if store.get_bytes(ROUTER_KEY) is None:
        data.train_router()
    else:
        print("router: present in object store")

    if db.count("policies") == 0:
        src = (settings.policies_dir / "v1.yaml").read_text()
        db.insert_policy(parse_policy(src).version, src, "published", "Initial policy from the assignment brief.")
        print("policy v1 seeded and published")

    if db.count("labels") == 0:
        legacy = settings.data_dir / "labeler.db"
        if legacy.exists():  # a maintainer's working copy: everything, including ad-hoc runs
            print(f"importing measured runs from {legacy}")
            import_legacy.main()
        else:  # a fresh clone: the measured runs shipped in data/seed
            seed.load()


if __name__ == "__main__":
    main()

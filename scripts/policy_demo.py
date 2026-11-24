"""Policy change demo: v2 changes behaviour with no code change.

    uv run python -m scripts.policy_demo            # draft v2 -> shadow-run on eval set -> compare -> publish
    uv run python -m scripts.policy_demo --no-publish

The same steps are buttons on the Policy page of the UI. Nothing here touches the
agent, the prompt or the schema: v2.yaml is read, stored as a version, and the
agent picks up its new section from the document.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter

from labeler.bootstrap import build_service
from labeler.config import get_settings


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="v2.yaml")
    ap.add_argument("--no-publish", action="store_true")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()

    s = get_settings()
    svc = build_service(s)
    source = (s.policies_dir / args.file).read_text()
    from labeler.labeling.policy import parse_policy

    v = parse_policy(source).version
    if not svc.db.get_policy_row(v):
        svc.create_draft(source, note="Adds §6 vague home-based clerical work (review only).")
        print(f"created draft {v}")
    new = svc.policy(v)
    old = svc.policy()  # currently published
    print(
        f"published: {old.version} ({len(old.sections)} sections) · draft: {new.version} ({len(new.sections)} sections)"
    )

    ids = svc.db.posting_ids(eval_only=True, limit=args.limit)
    print(f"shadow-running {new.version} on {len(ids)} eval postings (published {old.version} keeps serving)…")
    await svc.run_ids(new, ids, skip_existing=True)

    cmp = svc.compare(old.version, new.version)
    print(f"\ncompared {cmp['compared']} postings labeled under both versions")
    for k, n in sorted(cmp["transitions"].items(), key=lambda kv: -kv[1]):
        a, b = k.split("->")
        print(f"  {a:>13} -> {b:<13} {n:4d}{'' if a == b else '   *'}")
    hits = Counter(s for c in cmp["changed"] for s in c["to"]["sections"] if s not in c["from"]["sections"])
    print(f"sections newly cited on changed postings: {dict(hits)}")

    if not args.no_publish and svc.db.get_policy_row(v)["status"] == "draft":
        await svc.publish(v, backfill=False)  # the shadow run above already labeled the eval set
        print(f"\npublished {v}; API and UI now serve it. Re-label the last 30 days with:")
        print(
            f"  curl -XPOST localhost:8000/api/policies/{v}/relabel -H 'content-type: application/json' -d '{{\"scope\":\"recent\"}}'"
        )
        print("or publish from the UI with 'Re-label the last N days' ticked (N is on the Settings page).")


if __name__ == "__main__":
    asyncio.run(main())

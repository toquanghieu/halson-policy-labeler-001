"""Run the agent over the fixed 400-posting eval set and report metrics.

    uv run python -m scripts.run_eval                    # published policy, resumes if interrupted
    uv run python -m scripts.run_eval --version v2
    uv run python -m scripts.run_eval --tag opus-only --tier1 claude-opus-5-5 --no-escalation
    uv run python -m scripts.run_eval --tag slm --provider slm --tier1 slm-qwen2.5-1.5b-distilled --no-escalation

Results land in data/eval/<tag>.json. Labels are stored in the DB like any other
run, so the UI shows them. Runs with a non-default --tag keep their labels
under a separate version id (<version>@<tag>) so they never overwrite the main run.
"""

from __future__ import annotations

import argparse
import asyncio
import json

from labeler.bootstrap import build_service
from labeler.config import get_settings
from labeler.labeling.metrics import evaluate
from labeler.labeling.schemas import Label


def router_baseline(svc, ids: list[str], threshold: float = 0.5) -> dict:
    router = svc.agent.router
    if router is None:
        return {}
    rows = []
    for jid in ids:
        posting, gold, _ = svc.db.get_posting(jid)
        score = router.score(posting)
        rows.append(
            {
                "label": "VIOLATING" if score >= threshold else "ALLOWED",
                "gold": gold,
                "sections": [],
                "tier": "router",
                "cost_usd": 0.0,
                "latency_ms": 0.0,
            }
        )
    m = evaluate(rows)
    return {"threshold": threshold, "strict": m["strict"], "confusion": m["confusion"]}


def error_cases(svc, version: str, ids: list[str]) -> list[dict]:
    out = []
    for jid in ids:
        posting, gold, _ = svc.db.get_posting(jid)
        r = svc.db.get_label(jid, version)
        if r is None:
            continue
        wrong = (gold == 1 and r.label is Label.ALLOWED) or (gold == 0 and r.label is Label.VIOLATING)
        if not wrong:
            continue
        out.append(
            {
                "job_id": jid,
                "gold": "fraud" if gold else "legit",
                "label": r.label.value,
                "sections": r.policy_sections,
                "confidence": r.confidence,
                "rationale": r.rationale,
                "evidence": r.evidence,
                "title": posting.title,
                "has_company_profile": bool(posting.company_profile.strip()),
                "router_score": r.trace.get("router_score"),
                "tier": r.trace.get("tier"),
            }
        )
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", help="policy version (default: published)")
    ap.add_argument("--tag", default="main")
    ap.add_argument("--provider", choices=["anthropic", "claude_cli", "fake", "slm"], help="default: LLM_PROVIDER")
    ap.add_argument("--tier1")
    ap.add_argument("--tier2")
    ap.add_argument("--no-escalation", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--concurrency", type=int)
    args = ap.parse_args()

    settings = get_settings()
    overrides = {}
    if args.provider:
        overrides["llm_provider"] = args.provider
    if args.tier1:
        overrides["tier1_model"] = args.tier1
    if args.tier2:
        overrides["tier2_model"] = args.tier2
    if args.no_escalation:
        overrides["tier2_model"] = ""
    if args.concurrency:
        overrides["max_concurrency"] = args.concurrency
    settings = settings.model_copy(update=overrides)

    svc = build_service(settings)
    svc.max_concurrency = settings.max_concurrency
    policy = svc.policy(args.version)
    if args.tag != "main":
        # Store ablation runs under their own version id so they never overwrite the main run.
        policy = policy.model_copy(update={"version": f"{policy.version}@{args.tag}"})

    ids = svc.db.posting_ids(eval_only=True, limit=args.limit)
    print(
        f"labeling {len(ids)} eval postings with policy {policy.version} "
        f"(tier1={settings.tier1_model}, tier2={settings.tier2_model or 'off'}, concurrency={settings.max_concurrency})"
    )
    await svc.run_ids(policy, ids, skip_existing=True)

    rows = [r for r in svc.db.label_rows(policy.version, eval_only=True) if r["job_id"] in set(ids)]
    metrics = evaluate(rows)
    report = {
        "tag": args.tag,
        "policy_version": policy.version,
        "tier1": settings.tier1_model,
        "tier2": settings.tier2_model or None,
        "metrics": metrics,
        "router_only_baseline": router_baseline(svc, ids),
        "errors": error_cases(svc, policy.version, ids),
    }
    out = settings.data_dir / "eval" / f"{args.tag}-{policy.version.replace('@', '_')}.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(
        json.dumps(
            {
                k: metrics[k]
                for k in ("confusion", "strict", "flagged", "review_rate", "tiers", "cost_per_1k_usd", "latency_ms")
            },
            indent=2,
        )
    )
    print(f"wrote {out}")


if __name__ == "__main__":
    asyncio.run(main())

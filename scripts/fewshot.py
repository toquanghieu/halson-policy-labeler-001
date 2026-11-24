"""Few-shot examples for the prompt, picked from the 1,000-row development sample, measured on dev.

    uv run python -m scripts.fewshot build      # policies/v1.1.yaml = v1 + worked examples
    LLM_PROVIDER=claude_cli uv run python -m scripts.fewshot compare   # v1 vs v1.1 on 60 held-out dev rows

Examples come from the teacher run (scripts/distill.py: Claude's verified findings on the same
1,000 balanced dev rows the router trains on). One per violating section where one exists, one
§5, and an ALLOWED commission-only sales role (the brief's edge case). Each example is an
excerpt around the verified quotes, not the whole posting.

They live in the policy document (`examples:`), so adopting them is a policy change like any
other: v1.1 is a draft until shadow-run and published. The comparison runs on 60 development
postings (30 fraudulent, 30 legitimate) that are in neither the 1,000-row sample nor the eval
set, so nothing here is tuned on the eval set.
"""

from __future__ import annotations

import asyncio
import re
import sys
from typing import Any

import yaml

from labeler.bootstrap import build_service
from labeler.config import REPO_ROOT, get_settings
from labeler.labeling.metrics import evaluate
from labeler.labeling.policy import parse_policy
from scripts.data import eval_ids, read_dataset
from scripts.distill import POLICY, TEACHER_TAG, sample_ids, target

OUT = REPO_ROOT / "policies" / "v1.1.yaml"
HOLDOUT = 60
WINDOW = 120


def _excerpt(posting, spans, fallback_field: str = "description") -> str:
    parts = [f"[title] {posting.title}"]
    if not posting.company_profile.strip():
        parts.append("[company_profile] (empty)")
    for s in spans:
        if " ".join(s.quote.split()) in " ".join(" ".join(parts).split()):
            continue  # already inside an earlier window
        text = getattr(posting, s.field)
        a, b = max(0, s.start - WINDOW), min(len(text), s.end + WINDOW)
        chunk = " ".join(text[a:b].split())
        parts.append(f"[{s.field}] {'…' if a else ''}{chunk}{'…' if b < len(text) else ''}")
    if not spans:
        text = " ".join(getattr(posting, fallback_field).split())
        parts.append(f"[{fallback_field}] {text[:420]}{'…' if len(text) > 420 else ''}")
    return "\n".join(parts)


def pick_examples() -> list[dict[str, Any]]:
    svc = build_service(get_settings())
    rows = []
    for jid in sample_ids():
        r = svc.db.get_label(jid, f"{POLICY}@{TEACHER_TAG}")
        if r is None or r.trace.get("tier") == "failed":
            continue
        posting, gold, _ = svc.db.get_posting(jid)
        rows.append((jid, r, posting, gold))
    if len(rows) < 200:
        raise SystemExit(f"only {len(rows)} teacher labels; run `make distill-teacher` first")

    def best(pred):
        cands = [x for x in rows if pred(*x)]
        return max(cands, key=lambda x: (x[1].confidence, -len(x[2].description)), default=None)

    chosen = []
    for sid in ("§1", "§2", "§3", "§4"):
        hit = best(lambda _j, r, _p, g, sid=sid: g == 1 and sid in r.policy_sections and r.label.value == "VIOLATING")
        if hit:
            chosen.append(hit)
    review = best(lambda _j, r, _p, g: g == 1 and r.policy_sections == ["§5"])
    if review:
        chosen.append(review)
    commission = best(
        lambda _j, r, p, g: (
            g == 0 and r.label.value == "ALLOWED" and "commission" in (p.description + p.benefits).lower()
        )
    )
    if commission:
        chosen.append(commission)

    examples, used = [], set()
    for jid, r, posting, _gold in chosen:
        if jid in used:
            continue
        used.add(jid)
        t = target(r)
        kept = {f["section"] for f in t["findings"]}
        # Quotes are the verified spans cut from the original text (not the model's copy), and the
        # excerpt is built around them, so every example quote appears verbatim in its excerpt.
        spans_by_section: dict[str, list] = {}
        for sp in r.evidence_spans:
            if sp.section in kept and len(spans_by_section.setdefault(sp.section, [])) < 2:
                spans_by_section[sp.section].append(sp)
        spans = [sp for v in spans_by_section.values() for sp in v]
        excerpt = _excerpt(posting, spans)
        findings = []
        for f in t["findings"]:
            quotes = [sp.quote for sp in spans_by_section.get(f["section"], [])]
            assert all(" ".join(q.split()) in " ".join(excerpt.split()) for q in quotes), (jid, f["section"])
            first = f["explanation"].split(". ")[0].rstrip(".") + "."
            findings.append({"section": f["section"], "quotes": quotes, "facts": f["facts"], "explanation": first})
        examples.append(
            {
                "title": " ".join((posting.title or "(untitled)").split()),
                "excerpt": excerpt,
                "findings": findings,
                "label": r.label.value,
                "note": t["rationale"],
            }
        )
    svc.db.close()
    return examples


def build() -> None:
    src = (REPO_ROOT / "policies" / "v1.yaml").read_text()
    src = re.sub(r"^version:.*$", "version: v1.1", src, count=1, flags=re.M)
    examples = pick_examples()
    block = yaml.safe_dump({"examples": examples}, allow_unicode=True, sort_keys=False, width=100)
    header = (
        "\n# Worked examples (few-shot), picked by scripts/fewshot.py from Claude's verified findings on\n"
        "# development postings (never the eval set). Same sections and rules as v1.\n"
    )
    out = src.rstrip() + "\n" + header + block
    p = parse_policy(out)  # validates: example sections must exist in this policy
    OUT.write_text(out)
    print(f"wrote {OUT.relative_to(REPO_ROOT)}: {len(p.examples)} examples")
    for e in p.examples:
        print(f"  {e.label.value:<12} {[f.section for f in e.findings]}  {e.title[:60]}")


def holdout_ids() -> list[str]:
    df = read_dataset()
    taken = set(sample_ids()) | eval_ids()
    rest = df[~df["job_id"].astype(str).isin(taken)]
    parts = [rest[rest["fraudulent"] == c].sample(n=HOLDOUT // 2, random_state=11) for c in (1, 0)]
    return [str(x) for part in parts for x in part["job_id"]]


async def compare() -> None:
    settings = get_settings()
    svc = build_service(settings)
    base = svc.policy(POLICY).model_copy(update={"version": f"{POLICY}@fs-base"})
    fs = parse_policy(OUT.read_text())
    fs = fs.model_copy(update={"version": "v1.1@fs"})
    ids = holdout_ids()
    print(f"{len(ids)} held-out dev postings; {settings.tier1_model} -> {settings.tier2_model or 'off'}")
    for pol in (base, fs):
        await svc.run_ids(pol, ids, skip_existing=True)
    print(
        f"\n{'prompt':<22} {'flagged P':>9} {'flagged R':>9} {'strict P':>8} {'strict R':>8} {'review':>7} {'escal.':>7} {'$/1k':>6}"
    )
    for name, pol in (("v1 (no examples)", base), (f"v1.1 ({len(fs.examples)} examples)", fs)):
        rows = [r for r in svc.db.label_rows(pol.version) if r["job_id"] in set(ids)]
        m = evaluate(rows)
        esc = m["tiers"].get("tier2", 0) / max(1, m["n"])
        print(
            f"{name:<22} {m['flagged']['precision']:9.2f} {m['flagged']['recall']:9.2f} "
            f"{m['strict']['precision']:8.2f} {m['strict']['recall']:8.2f} {m['review_rate']:7.1%} {esc:7.1%} "
            f"{m['cost_per_1k_usd']:6.2f}"
        )
        print("   confusion:", {k: v for k, v in m["confusion"].items()})
    svc.db.close()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    if cmd == "build":
        build()
    elif cmd == "compare":
        asyncio.run(compare())
    else:
        raise SystemExit(f"unknown command {cmd!r}")

"""Router trained on 1,000 balanced dev rows vs on all 17,480 dev rows, measured on the eval set.

    uv run python -m scripts.router_compare

The router only routes (it decides who gets a tier-2 second opinion), so what matters is
(a) how well it ranks frauds, and (b) how many escalations it would trigger. (b) is computed
without any model call: the stored eval run (data/seed, policy v1, first prompt) keeps each
posting's tier-1 answer and its other escalation reasons, so we can replay the escalation rule
with either router's score. Nothing is tuned on the eval set; this only reports.
"""

from __future__ import annotations

import gzip
import json

import numpy as np
from sklearn.metrics import average_precision_score, roc_auc_score

from labeler.config import get_settings
from labeler.labeling.router import router_text
from scripts.data import ROUTER_SAMPLE, dev_rows, eval_ids, fit_router, read_dataset, to_posting

THRESHOLD = 0.5  # router_disagree_threshold used in the eval run


def prf(y: np.ndarray, flag: np.ndarray) -> tuple[float, float, float]:
    tp = int((flag & (y == 1)).sum())
    p = tp / max(1, int(flag.sum()))
    r = tp / max(1, int((y == 1).sum()))
    return p, r, 2 * p * r / max(1e-9, p + r)


def main() -> None:
    df = read_dataset()
    ev = df[df["job_id"].astype(str).isin(eval_ids())]
    ids = ev["job_id"].astype(str).tolist()
    y = ev["fraudulent"].astype(int).to_numpy()
    texts = [router_text(to_posting(r)) for _, r in ev.iterrows()]

    with gzip.open(get_settings().data_dir / "seed" / "seed.json.gz", "rt", encoding="utf-8") as f:
        runs = {
            r["result"]["job_id"]: r["result"]
            for r in json.load(f)["labels"]
            if r["result"]["policy_version"] == "v1" and r["result"]["job_id"] in set(ids)
        }

    print(f"eval set: {len(ids)} postings ({int(y.sum())} fraudulent); stored v1 decisions: {len(runs)}\n")
    rows = []
    for name, sample in (("all dev rows", 0), (f"{ROUTER_SAMPLE} balanced", ROUTER_SAMPLE)):
        pipe = fit_router(dev_rows(df, sample))
        score = pipe.predict_proba(texts)[:, 1]
        p, r, f1 = prf(y, score >= THRESHOLD)

        escalated = missed_flagged = 0
        for jid, s in zip(ids, score, strict=True):
            res = runs.get(jid)
            if not res:
                continue
            t = res["trace"]
            other = [x for x in t.get("escalation_reasons", []) if not x.startswith("router")]
            t1 = t["steps"][0].get("label")
            esc = bool(other) or (s >= THRESHOLD and t1 == "ALLOWED")
            escalated += esc
        for jid, s, g in zip(ids, score, y, strict=True):
            res = runs.get(jid)
            if res and g == 1 and res["label"] == "ALLOWED" and s >= THRESHOLD:
                missed_flagged += 1
        rows.append(
            (
                name,
                average_precision_score(y, score),
                roc_auc_score(y, score),
                p,
                r,
                f1,
                escalated / len(runs),
                missed_flagged,
            )
        )

    print(
        f"\n{'router':<16} {'AP':>6} {'AUC':>6} {'P@0.5':>6} {'R@0.5':>6} {'F1':>6} {'escal.':>7} {'missed→flagged':>15}"
    )
    for name, ap, auc, p, r, f1, esc, mf in rows:
        print(f"{name:<16} {ap:6.3f} {auc:6.3f} {p:6.2f} {r:6.2f} {f1:6.2f} {esc:7.1%} {mf:>9} / 56")
    print(
        "\nescal. = share of the eval set that would reach tier 2 with this router (replayed from the stored run).\n"
        "missed→flagged = of the frauds labeled ALLOWED, how many this router scores >= 0.5 (they get a second opinion)."
    )


if __name__ == "__main__":
    main()

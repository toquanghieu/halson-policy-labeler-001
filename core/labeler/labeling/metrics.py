"""Evaluation metrics against EMSCAD's binary gold label.

NEEDS_REVIEW is neither a yes nor a no, so it is reported explicitly and scored
two ways:

  strict   only VIOLATING counts as "fraud" (what the system blocks on its own)
  flagged  VIOLATING or NEEDS_REVIEW counts as "fraud" (what reaches a human or is blocked)

The confusion matrix keeps all three predicted labels so nothing is hidden.
"""

from __future__ import annotations

from statistics import quantiles
from typing import Any


def _prf(tp: int, fp: int, fn: int) -> dict[str, float]:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    return {"precision": round(p, 3), "recall": round(r, 3), "f1": round(f1, 3), "tp": tp, "fp": fp, "fn": fn}


def _pct(values: list[float], q: int) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return round(values[0], 1)
    return round(quantiles(values, n=100, method="inclusive")[q - 1], 1)


def evaluate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """rows: dicts with label, gold (0/1), sections, tier, cost_usd, latency_ms."""
    scored = [r for r in rows if r.get("gold") is not None]
    matrix = {lab: {"fraud": 0, "legit": 0} for lab in ("VIOLATING", "NEEDS_REVIEW", "ALLOWED")}
    for r in scored:
        matrix[r["label"]]["fraud" if r["gold"] == 1 else "legit"] += 1

    v, nr, al = matrix["VIOLATING"], matrix["NEEDS_REVIEW"], matrix["ALLOWED"]
    strict = _prf(tp=v["fraud"], fp=v["legit"], fn=nr["fraud"] + al["fraud"])
    flagged = _prf(tp=v["fraud"] + nr["fraud"], fp=v["legit"] + nr["legit"], fn=al["fraud"])

    sections: dict[str, dict[str, int]] = {}
    for r in scored:
        for s in r.get("sections", []):
            d = sections.setdefault(s, {"fraud": 0, "legit": 0})
            d["fraud" if r["gold"] == 1 else "legit"] += 1

    costs = [r.get("cost_usd", 0.0) for r in rows]
    lat = [r.get("latency_ms", 0.0) for r in rows if r.get("tier") != "failed"]
    tiers: dict[str, int] = {}
    for r in rows:
        tiers[r.get("tier", "?")] = tiers.get(r.get("tier", "?"), 0) + 1

    n = len(rows)
    return {
        "n": n,
        "n_scored": len(scored),
        "confusion": matrix,
        "strict": strict,
        "flagged": flagged,
        "review_rate": round((nr["fraud"] + nr["legit"]) / len(scored), 3) if scored else 0.0,
        "sections": sections,
        "tiers": tiers,
        "escalation_rate": round(tiers.get("tier2", 0) / n, 3) if n else 0.0,
        "cost_per_1k_usd": round(sum(costs) / n * 1000, 4) if n else 0.0,
        # Decisions made by a model with no list price (cost counted as 0): the cost above is a floor.
        "cost_unpriced": sum(1 for r in rows if r.get("cost_unpriced")),
        # How the token counts behind the cost were measured: "api", or "claude_cli" (overstated).
        "cost_basis": sorted({r.get("cost_basis", "api") for r in rows}),
        "total_cost_usd": round(sum(costs), 4),
        "latency_ms": {"p50": _pct(lat, 50), "p95": _pct(lat, 95), "max": round(max(lat), 1) if lat else 0.0},
    }

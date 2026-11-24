from labeler.labeling.metrics import evaluate


def row(label, gold, tier="tier1", cost=0.001, lat=1000.0, sections=()):
    return {"label": label, "gold": gold, "tier": tier, "cost_usd": cost, "latency_ms": lat, "sections": list(sections)}


def test_needs_review_is_scored_both_ways():
    rows = [
        row("VIOLATING", 1),
        row("VIOLATING", 0),
        row("NEEDS_REVIEW", 1),
        row("NEEDS_REVIEW", 0),
        row("ALLOWED", 1),
        row("ALLOWED", 0),
        row("ALLOWED", 0),
    ]
    m = evaluate(rows)
    assert m["confusion"]["NEEDS_REVIEW"] == {"fraud": 1, "legit": 1}
    # strict: only VIOLATING is a positive
    assert (m["strict"]["tp"], m["strict"]["fp"], m["strict"]["fn"]) == (1, 1, 2)
    # flagged: VIOLATING or NEEDS_REVIEW is a positive
    assert (m["flagged"]["tp"], m["flagged"]["fp"], m["flagged"]["fn"]) == (2, 2, 1)
    assert m["strict"]["recall"] == round(1 / 3, 3)
    assert m["review_rate"] == round(2 / 7, 3)


def test_cost_and_latency_summary():
    rows = [row("ALLOWED", 0, cost=0.002, lat=float(i)) for i in range(1, 101)]
    rows.append(row("NEEDS_REVIEW", 0, tier="failed", cost=0.0, lat=99999.0))
    m = evaluate(rows)
    assert m["latency_ms"]["p50"] == 50.5
    assert m["latency_ms"]["max"] == 100.0  # failed items excluded from latency
    assert m["cost_per_1k_usd"] == round(0.2 / 101 * 1000, 4)

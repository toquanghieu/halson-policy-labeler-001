"""Prometheus metrics: the production drift signals from DESIGN.md §5, measured where they happen.

The API serves them at /metrics; each worker on its own port (METRICS_PORT). Locally Prometheus
scrapes both and Grafana charts them (`docker compose --profile observability up`); on AWS the
same names go to CloudWatch through the agent's Prometheus scraper.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

from labeler.labeling.schemas import LabelResult

DECISIONS = Counter("labeler_decisions_total", "Labels produced", ["policy_version", "label", "tier"])
SECTIONS = Counter("labeler_sections_total", "Policy sections cited in decisions", ["policy_version", "section"])
ESCALATIONS = Counter("labeler_escalations_total", "Decisions that went to tier 2", ["reason"])
QUOTES = Counter("labeler_quotes_total", "Model quotes by verification outcome", ["outcome"])
DROPPED = Counter("labeler_sections_dropped_total", "Sections dropped for lack of verified evidence")
COST = Counter("labeler_llm_cost_usd_total", "Model spend at list price", ["model"])
LATENCY = Histogram(
    "labeler_decision_seconds",
    "End-to-end time to label one posting",
    buckets=(1, 2, 3, 5, 8, 13, 20, 30, 45, 60, 90),
)
QUEUE_DEPTH = Gauge("labeler_queue_messages", "Messages waiting or in flight", ["queue", "state"])
WORKER_MESSAGES = Counter("labeler_worker_messages_total", "Queue messages handled", ["queue", "outcome"])


def record_decision(r: LabelResult) -> None:
    t = r.trace
    DECISIONS.labels(r.policy_version, r.label.value, t.get("tier", "")).inc()
    for s in r.policy_sections:
        SECTIONS.labels(r.policy_version, s).inc()
    for reason in t.get("escalation_reasons", []):
        # Bucket free-text reasons ("confidence 0.50 < 0.6") into a small label set.
        ESCALATIONS.labels(reason.split(" ")[0] if reason else "unknown").inc()
    for step in t.get("steps", []):
        rejected = len(step.get("rejected_quotes", []))
        quoted = sum(len(f.get("quotes", [])) for f in (step.get("raw") or {}).get("findings", []))
        QUOTES.labels("verified").inc(max(0, quoted - rejected))
        QUOTES.labels("rejected").inc(rejected)
        DROPPED.inc(len(step.get("dropped_sections", [])))
        if step.get("model"):
            COST.labels(step["model"]).inc(step.get("cost_usd", 0.0))
    LATENCY.observe(t.get("latency_ms", 0.0) / 1000)

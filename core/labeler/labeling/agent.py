"""The labeling agent: a code-orchestrated workflow around one structured LLM call.

    signals ─► router score ─► tier-1 LLM ─► verify evidence ─► aggregate label
                                                   │
                               escalation trigger? ┴─► tier-2 LLM ─► verify ─► aggregate

Every step writes to `trace`, so a single decision can be replayed and debugged
from the UI. The model proposes sections and quotes; code decides what counts.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from labeler.config import Settings
from labeler.infra.llm.port import LLMError, LLMProvider, LLMRequest, LLMResponse, is_priced
from labeler.labeling.evidence import verify
from labeler.labeling.policy import Policy, policy_fingerprint
from labeler.labeling.prompt import output_schema, system_prompt, user_prompt
from labeler.labeling.router import Router
from labeler.labeling.schemas import Evidence, Label, LabelResult, Posting
from labeler.labeling.signals import extract_signals, metadata_flags, signals_as_dicts

REJECTED_QUOTE_PENALTY = 0.85
DROPPED_SECTION_PENALTY = 0.7


log = logging.getLogger(__name__)


class _Finding(BaseModel):
    model_config = ConfigDict(extra="ignore")
    section: str
    quotes: list[str] = []
    facts: list[str] = []
    explanation: str = ""


class ModelReply(BaseModel):
    """The shape the schema asks for, checked in code: structured output is not guaranteed by every
    provider (an OpenAI-compatible server may ignore json_schema; the local student has none)."""

    model_config = ConfigDict(extra="ignore")
    findings: list[_Finding] = []
    insufficient_evidence: bool = False
    rationale: str = ""
    confidence: float = Field(0.5, allow_inf_nan=False)


RAW_KEEP_CHARS = 8000  # enough to read a bad reply in the trace without storing an essay


def keep_raw(data: Any) -> Any:
    """The model's output as returned, for the trace; very long text is cut."""
    if isinstance(data, str) and len(data) > RAW_KEEP_CHARS:
        return data[:RAW_KEEP_CHARS] + f"… [{len(data) - RAW_KEEP_CHARS} more characters]"
    return data


def validate_reply(data: Any) -> dict[str, Any]:
    try:
        return ModelReply.model_validate(data).model_dump()
    except ValidationError as e:
        first = e.errors()[0]
        where = ".".join(str(x) for x in first["loc"]) or "reply"
        raise LLMError(f"malformed model output at {where}: {first['msg']}", retryable=True, raw=keep_raw(data)) from e


_WARNED_UNPRICED: set[str] = set()


@dataclass
class Assessment:
    """One model pass, after verification."""

    model: str
    label: Label
    sections: list[str]
    evidence: list[Evidence]
    facts: dict[str, list[str]]
    rationale: str
    confidence: float
    insufficient_evidence: bool
    rejected_quotes: list[dict[str, str]]
    dropped_sections: list[dict[str, str]]
    # Set when the sections combined to VIOLATING but no quote survived: facts alone never block.
    downgraded: str | None
    raw: dict[str, Any]
    cost_usd: float
    latency_ms: float
    usage: dict[str, int]


def assess(policy: Policy, posting: Posting, facts: dict[str, bool], resp: LLMResponse) -> Assessment:
    data = resp.data
    sections: list[str] = []
    evidence: list[Evidence] = []
    section_facts: dict[str, list[str]] = {}
    rejected: list[dict[str, str]] = []
    dropped: list[dict[str, str]] = []

    # Several findings for one section are one claim: their quotes and facts are pooled.
    merged: dict[str, dict[str, list[str]]] = {}
    for f in data.get("findings", []):
        sid = f.get("section")
        if policy.section(sid) is None:
            continue
        m = merged.setdefault(sid, {"quotes": [], "facts": []})
        m["quotes"] += [q for q in f.get("quotes", []) if q not in m["quotes"]]
        m["facts"] += [n for n in f.get("facts", []) if n not in m["facts"]]

    for sid, m in merged.items():
        section = policy.section(sid)
        if section is None:  # filtered above; keeps the type checker honest
            continue
        ok, bad = verify(posting, sid, m["quotes"])
        rejected += [{"section": sid, "quote": q} for q in bad]
        # Grounding rule: a VIOLATING-effect section needs at least one verbatim quote. An
        # absence fact can support only a review-only section that accepts that fact.
        true_facts = [n for n in m["facts"] if facts.get(n) and section.takes_fact(n)]
        if ok or true_facts:
            sections.append(sid)
            evidence += ok
            if true_facts:
                section_facts[sid] = true_facts
        else:
            if m["quotes"]:
                reason = "no quote found in posting"
            elif m["facts"]:
                reason = "the facts given do not support this section on their own"
            else:
                reason = "no quote given"
            dropped.append({"section": sid, "reason": reason})

    insufficient = bool(data.get("insufficient_evidence", False))
    label = policy.aggregate(sections, insufficient)
    downgraded = None
    if label is Label.VIOLATING and not evidence:
        # Policy edge case: missing fields are never the sole basis for VIOLATING. Two review-only
        # sections resting on facts alone combine to "VIOLATING" by count; that goes to a human.
        downgraded = "VIOLATING by section count, but no quote was verified: facts alone never block"
        label = Label.NEEDS_REVIEW

    confidence = float(data.get("confidence", 0.5))
    confidence = min(max(confidence, 0.0), 1.0)
    if rejected:
        confidence *= REJECTED_QUOTE_PENALTY
    if dropped:
        confidence *= DROPPED_SECTION_PENALTY

    return Assessment(
        model=resp.model,
        label=label,
        sections=sorted(sections, key=policy.section_ids.index),
        evidence=evidence,
        facts=section_facts,
        rationale=str(data.get("rationale", "")).strip(),
        confidence=round(confidence, 3),
        insufficient_evidence=insufficient,
        rejected_quotes=rejected,
        dropped_sections=dropped,
        downgraded=downgraded,
        raw=resp.extra.get("raw", data),
        cost_usd=resp.usage.cost_usd(resp.model),
        latency_ms=round(resp.latency_ms, 1),
        usage=vars(resp.usage),
    )


def escalation_reasons(a: Assessment, router_score: float | None, s: Settings) -> list[str]:
    reasons = []
    if a.confidence < s.escalate_below_confidence:
        reasons.append(f"confidence {a.confidence:.2f} < {s.escalate_below_confidence}")
    if a.dropped_sections:
        reasons.append("a reported section had no verifiable evidence")
    if a.downgraded:
        reasons.append("sections combined to VIOLATING without a verified quote")
    if a.insufficient_evidence:
        reasons.append("model reported insufficient evidence")
    if router_score is not None and router_score >= s.router_disagree_threshold and a.label is Label.ALLOWED:
        reasons.append(f"router risk {router_score:.2f} disagrees with ALLOWED")
    return reasons


class LabelingAgent:
    def __init__(
        self,
        provider: LLMProvider | Callable[[str], LLMProvider],
        settings: Settings,
        router: Router | None = None,
    ):
        """`provider`: one provider for every tier, or a lookup by provider name (the registry),
        so each tier can run on a different vendor (Settings page)."""
        self.providers: Callable[[str], LLMProvider] = (
            (lambda _name: provider) if hasattr(provider, "complete") else provider  # type: ignore[assignment,return-value]
        )
        self.settings = settings
        self.router = router

    async def _call(self, req: LLMRequest, provider: str, deadline: float, attempts: int = 2) -> LLMResponse:
        """One tier's call: output validated, retried once on a retryable error, never past `deadline`
        (time.monotonic())."""
        last: LLMError | None = None
        for i in range(attempts):
            left = deadline - time.monotonic()
            if left <= 0:
                break
            try:
                resp = await asyncio.wait_for(self.providers(provider).complete(req), timeout=left)
                original = resp.data
                resp.data = validate_reply(original)
                resp.extra["raw"] = original  # the trace keeps what the model said, not the normalised copy
                return resp
            except TimeoutError:
                last = LLMError("per-posting deadline exceeded")
                break
            except LLMError as e:
                last = e
                if not e.retryable or i == attempts - 1:
                    break
                await asyncio.sleep(min(0.5 * (2**i), max(0.0, deadline - time.monotonic())))
        raise last or LLMError("per-posting deadline exceeded")

    async def label(self, posting: Posting, policy: Policy, settings: Settings | None = None) -> LabelResult:
        """`settings` carries the run-time overrides (models, thresholds); default: the ones built with."""
        started = time.perf_counter()
        s = settings or self.settings
        deadline = time.monotonic() + getattr(s, "label_deadline_s", 240.0)
        facts = metadata_flags(posting)
        hints = extract_signals(posting)
        router_score = round(self.router.score(posting), 4) if self.router else None

        system = system_prompt(policy)
        user = user_prompt(posting, facts, hints)
        schema = output_schema(policy)
        trace: dict[str, Any] = {
            # The exact policy content this decision was made under (drafts can change between runs).
            "policy_sha": policy_fingerprint(policy),
            "facts": facts,
            "hints": signals_as_dicts(hints),
            "router_score": router_score,
            "steps": [],
        }

        p1, p2 = s.provider_for(1), s.provider_for(2)
        try:
            resp1 = await self._call(LLMRequest(s.tier1_model, system, user, schema, s.tier1_effort), p1, deadline)
            a1 = assess(policy, posting, facts, resp1)
        except LLMError as e:
            return self._failed(posting, policy, trace, str(e), started, raw=e.raw)
        except Exception as e:  # a bug must still end as a label a human sees, not a crashed message
            log.exception("labeling %s failed unexpectedly", posting.job_id)
            return self._failed(posting, policy, trace, f"internal error: {type(e).__name__}: {e}", started)

        trace["steps"].append({**self._step("tier1", a1), "provider": p1})
        final = a1

        reasons = escalation_reasons(a1, router_score, s)
        trace["escalation_reasons"] = reasons
        if reasons and s.tier2_model and (p2, s.tier2_model) != (p1, s.tier1_model):
            try:
                resp2 = await self._call(
                    LLMRequest(s.tier2_model, system, user, schema, s.tier2_effort, max_tokens=8000), p2, deadline
                )
                a2 = assess(policy, posting, facts, resp2)
                trace["steps"].append({**self._step("tier2", a2), "provider": p2})
                final = a2
            except Exception as e:  # LLMError, or a bug: keep the tier-1 answer
                if not isinstance(e, LLMError):
                    log.exception("tier 2 for %s failed unexpectedly", posting.job_id)
                # Keep the tier-1 answer but make the failure visible and lower trust.
                raw = e.raw if isinstance(e, LLMError) else None
                trace["steps"].append({"tier": "tier2", "error": str(e), **({"raw": raw} if raw is not None else {})})
                final.confidence = round(final.confidence * 0.8, 3)

        trace["tier"] = "tier2" if final is not a1 else "tier1"
        trace["cost_usd"] = round(sum(st.get("cost_usd", 0.0) for st in trace["steps"]), 6)
        # Token counts from the local Claude CLI include its own wrapper and cache writes, so a cost
        # computed from them overstates what the API would bill (RESULTS.md §6). Say which it is.
        trace["cost_basis"] = (
            "claude_cli" if any(st.get("provider") == "claude_cli" for st in trace["steps"]) else "api"
        )
        unpriced = sorted({st["model"] for st in trace["steps"] if st.get("model") and not is_priced(st["model"])})
        if unpriced:  # cost_usd above counts these as 0: say so instead of reporting "free"
            trace["cost_unpriced"] = unpriced
            for m in set(unpriced) - _WARNED_UNPRICED:
                _WARNED_UNPRICED.add(m)
                log.warning("model %s has no list price: its cost is recorded as 0 and flagged", m)
        trace["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
        trace["section_facts"] = final.facts

        return LabelResult(
            job_id=posting.job_id,
            label=final.label,
            policy_sections=final.sections,
            # One entry per distinct quote: two sections citing the same sentence share it.
            evidence=list(dict.fromkeys(e.quote for e in final.evidence)),
            rationale=final.rationale or "No rationale returned.",
            confidence=final.confidence,
            policy_version=policy.version,
            evidence_spans=final.evidence,
            trace=trace,
        )

    @staticmethod
    def _step(tier: str, a: Assessment) -> dict[str, Any]:
        return {
            "tier": tier,
            "model": a.model,
            "label": a.label.value,
            "sections": a.sections,
            "confidence": a.confidence,
            "rejected_quotes": a.rejected_quotes,
            "dropped_sections": a.dropped_sections,
            "downgraded": a.downgraded,
            "raw": a.raw,
            "usage": a.usage,
            "cost_usd": round(a.cost_usd, 6),
            "latency_ms": a.latency_ms,
        }

    @staticmethod
    def _failed(
        posting: Posting, policy: Policy, trace: dict[str, Any], error: str, started: float, raw: Any = None
    ) -> LabelResult:
        """Never drop an item: a failure becomes a NEEDS_REVIEW a human will see."""
        trace["steps"].append({"tier": "tier1", "error": error, **({"raw": raw} if raw is not None else {})})
        trace["tier"] = "failed"
        trace["cost_usd"] = 0.0
        trace["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
        return LabelResult(
            job_id=posting.job_id,
            label=Label.NEEDS_REVIEW,
            policy_sections=[],
            evidence=[],
            rationale=f"Automatic labeling failed ({error}); routed to manual review.",
            confidence=0.0,
            policy_version=policy.version,
            trace=trace,
        )

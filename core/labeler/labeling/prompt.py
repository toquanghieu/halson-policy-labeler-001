"""Prompt and output schema, both built from the policy document at run time."""

from __future__ import annotations

import json
from typing import Any

from labeler.labeling.policy import FACT_NAMES as POLICY_FACT_NAMES
from labeler.labeling.policy import Policy
from labeler.labeling.schemas import TEXT_FIELDS, Posting
from labeler.labeling.signals import Signal

# Re-exported: the facts block and the schema enum use the same names the policy validates.
FACT_NAMES = POLICY_FACT_NAMES

INSTRUCTIONS = """\
You are a Trust & Safety analyst at a job marketplace. You label one job posting
against the written policy below. The policy is the only rulebook: do not apply
rules that are not in it, and do not invent sections.

How to work:
1. Read the whole posting, including the metadata and the facts block.
2. For each policy section, decide whether the posting matches it.
3. For every section you report, copy one to three short quotes (5 to 30 words
   each) character-for-character from the posting text that prove the match.
   Quotes are checked mechanically against the posting; a quote that is
   paraphrased, shortened with "..." or stitched together is discarded, and a
   section left with no valid quote is dropped.
4. Missing information is not a quote. If a section rests on something being
   absent (for example an empty company profile), list it under `facts` using
   only the names given in the facts block. Facts alone are weak evidence and can
   never be the sole basis for a section whose effect is VIOLATING, and never the
   sole basis for a VIOLATING label: that needs at least one verified quote.
   They are exactly what review-only sections are for: when a review-only
   section's text names a condition that the facts block shows is true,
   report that section with the fact (only a fact the section lists as
   accepted, when it lists any), unless the posting text itself clearly
   satisfies what the section asks for. Policy guidance that missing fields
   are "weak signals" limits what can be VIOLATING; it does not stop a
   review-only section from matching.
5. Hints come from simple pattern matching and are often wrong. Use them to know
   where to look, never as evidence on their own.
6. Set `insufficient_evidence` to true only when the posting genuinely leaves you
   unable to decide whether a VIOLATING section applies. Do not use it for
   postings that are merely sparse but show no warning sign.
7. `rationale`: one or two plain sentences an analyst can read in five seconds,
   naming the sections that apply (or why none do).
8. `confidence`: your probability, from 0 to 1, that the label implied by your
   findings is what a careful senior analyst would decide under this policy.

You do not output the final label. It is computed from the sections whose
evidence survives verification, using each section's effect as stated in the
policy.
"""


def render_examples(policy: Policy) -> str:
    """Worked examples from the policy document (few-shot). Excerpts, not whole postings."""
    out = []
    for i, e in enumerate(policy.examples, 1):
        findings = [f.model_dump() for f in e.findings]
        out.append(
            f'<example n="{i}" title="{e.title}">\n{e.excerpt.strip()}\n'
            f"findings: {json.dumps(findings, ensure_ascii=False)}\n"
            f"resulting label: {e.label.value}" + (f"\nwhy: {e.note}" if e.note else "") + "\n</example>"
        )
    return "\n".join(out)


def system_prompt(policy: Policy) -> str:
    base = f"{INSTRUCTIONS}\n<policy>\n{policy.render()}\n</policy>"
    if not policy.examples:
        return base
    return (
        f"{base}\n\nWorked examples under this policy (excerpts of past postings, with the findings an "
        f"analyst confirmed). They show the standard of evidence; they are not rules.\n"
        f"<examples>\n{render_examples(policy)}\n</examples>"
    )


def output_schema(policy: Policy) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "section": {"type": "string", "enum": policy.section_ids},
                        "quotes": {"type": "array", "items": {"type": "string"}},
                        "facts": {"type": "array", "items": {"type": "string", "enum": list(FACT_NAMES)}},
                        "explanation": {"type": "string"},
                    },
                    "required": ["section", "quotes", "facts", "explanation"],
                    "additionalProperties": False,
                },
            },
            "insufficient_evidence": {"type": "boolean"},
            "rationale": {"type": "string"},
            "confidence": {"type": "number"},
        },
        "required": ["findings", "insufficient_evidence", "rationale", "confidence"],
        "additionalProperties": False,
    }


def user_prompt(posting: Posting, facts: dict[str, bool], hints: list[Signal]) -> str:
    parts = [f'<posting job_id="{posting.job_id}">']
    for field in TEXT_FIELDS:
        value = (getattr(posting, field) or "").strip()
        parts.append(f"[{field}]\n{value if value else '(empty)'}")
    meta = {
        "location": posting.location,
        "department": posting.department,
        "salary_range": posting.salary_range,
        "employment_type": posting.employment_type,
        "required_experience": posting.required_experience,
        "required_education": posting.required_education,
        "industry": posting.industry,
        "function": posting.function,
        "telecommuting": "yes" if posting.telecommuting else "no",
    }
    parts.append("[metadata]\n" + "\n".join(f"{k}: {v or '(empty)'}" for k, v in meta.items()))
    parts.append("</posting>")
    parts.append("<facts>\n" + "\n".join(f"{k}: {str(v).lower()}" for k, v in facts.items()) + "\n</facts>")
    if hints:
        lines = [f'- {h.kind} in {h.field}: "{h.text}"' for h in hints]
        parts.append("<hints>\n" + "\n".join(lines) + "\n</hints>")
    else:
        parts.append("<hints>\n(none)\n</hints>")
    return "\n\n".join(parts)

"""Policy documents: parse, validate, render for the model, and aggregate a label.

The policy is data. The agent learns which sections exist, what they say and how
each one affects the final label only from the document passed in here.
"""

from __future__ import annotations

from enum import Enum

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from labeler.errors import PolicyError
from labeler.labeling.schemas import Label

# Absence-of-information facts computed from the posting's metadata (signals.metadata_flags).
FACT_NAMES = ("no_company_profile", "no_company_logo", "no_salary", "no_screening_questions")


class Effect(str, Enum):
    VIOLATING = "violating"
    REVIEW_ONLY = "review_only"


class Section(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    effect: Effect
    text: str = Field(min_length=1)
    # Which absence facts may support this section on their own (no quote). None: any fact, for
    # versions written before the field existed; []: none. Only review-only sections may list
    # facts: a VIOLATING section always needs a verbatim quote.
    accepts_facts: list[str] | None = None

    @field_validator("text", "name")
    @classmethod
    def _strip(cls, v: str) -> str:
        return " ".join(v.split())

    @model_validator(mode="after")
    def _facts(self) -> Section:
        unknown = [f for f in self.accepts_facts or [] if f not in FACT_NAMES]
        if unknown:
            raise ValueError(f"{self.id}: unknown facts {unknown}; known: {list(FACT_NAMES)}")
        if self.accepts_facts and self.effect is Effect.VIOLATING:
            raise ValueError(f"{self.id}: a violating section cannot rest on facts; it needs a quote")
        return self

    def takes_fact(self, name: str) -> bool:
        if self.effect is not Effect.REVIEW_ONLY:
            return False
        return self.accepts_facts is None or name in self.accepts_facts


class ExampleFinding(BaseModel):
    section: str
    quotes: list[str] = []
    facts: list[str] = []
    explanation: str = ""


class Example(BaseModel):
    """A worked example shown to the model (few-shot). Versioned with the policy it illustrates,
    so changing the examples is a policy change: draft, shadow-run, publish, roll back."""

    title: str
    excerpt: str = Field(min_length=1)
    findings: list[ExampleFinding] = []
    label: Label
    note: str = ""


class Policy(BaseModel):
    version: str = Field(pattern=r"^[A-Za-z0-9._-]{1,32}$")
    title: str
    summary: str = ""
    sections: list[Section] = Field(min_length=1)
    edge_cases: list[str] = []
    examples: list[Example] = []
    # How matched sections combine, from the document, not the code: this many distinct matched
    # sections (of any effect) make the posting VIOLATING. 2 encodes v1's "On its own, §5 yields
    # NEEDS_REVIEW ... unless combined with another section". null: only `violating` sections do.
    combine_to_violating: int | None = Field(2, ge=2)

    @model_validator(mode="after")
    def _unique_ids(self) -> Policy:
        ids = [s.id for s in self.sections]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate section ids: {ids}")
        unknown = {f.section for e in self.examples for f in e.findings} - set(ids)
        if unknown:
            raise ValueError(f"examples cite sections not in this policy: {sorted(unknown)}")
        return self

    @property
    def section_ids(self) -> list[str]:
        return [s.id for s in self.sections]

    def section(self, section_id: str) -> Section | None:
        return next((s for s in self.sections if s.id == section_id), None)

    def render(self) -> str:
        """Policy text exactly as the model sees it."""
        lines = [f"# {self.title} (version {self.version})", "", " ".join(self.summary.split()), ""]
        for s in self.sections:
            effect = (
                "a match makes the posting VIOLATING"
                if s.effect is Effect.VIOLATING
                else "on its own a match yields NEEDS_REVIEW; combined with any other matched section it is VIOLATING"
            )
            lines += [f"## {s.id} {s.name}", s.text, f"(Effect: {effect}.)"]
            if s.effect is Effect.REVIEW_ONLY and s.accepts_facts is not None:
                lines.append(
                    f"(Can rest on these facts without a quote: {', '.join(s.accepts_facts)}.)"
                    if s.accepts_facts
                    else "(Needs a quote; no fact supports it on its own.)"
                )
            lines.append("")
        if self.combine_to_violating:
            lines += [
                "## How sections combine",
                f"{self.combine_to_violating} or more distinct matched sections make the posting VIOLATING, "
                "whatever their individual effect.",
                "",
            ]
        if self.edge_cases:
            lines.append("## Edge cases")
            lines += [f"- {e}" for e in self.edge_cases]
        return "\n".join(lines).strip()

    def aggregate(self, matched_sections: list[str], insufficient_evidence: bool) -> Label:
        """Deterministic label from the sections whose evidence survived verification.

        Kept in code, but driven entirely by each section's `effect` from the document.
        """
        sections = {sec.id: sec for s in matched_sections if (sec := self.section(s))}
        known = set(sections)
        effects = {sec.effect for sec in sections.values()}
        # The combination rule comes from the document (combine_to_violating), not from here.
        if Effect.VIOLATING in effects or (self.combine_to_violating and len(known) >= self.combine_to_violating):
            return Label.VIOLATING  # the caller still requires a verified quote (agent.assess)
        if Effect.REVIEW_ONLY in effects or insufficient_evidence:
            return Label.NEEDS_REVIEW
        return Label.ALLOWED


def policy_fingerprint(policy: Policy) -> str:
    """Short hash of everything in the policy that reaches the model or the label rule."""
    import hashlib

    return hashlib.sha256(policy.model_dump_json().encode()).hexdigest()[:12]


def parse_policy(source: str) -> Policy:
    try:
        data = yaml.safe_load(source)
    except yaml.YAMLError as e:
        raise PolicyError(f"invalid YAML: {e}") from e
    if not isinstance(data, dict):
        raise PolicyError("policy must be a YAML mapping")
    try:
        return Policy.model_validate(data)
    except ValidationError as e:
        raise PolicyError(str(e)) from e

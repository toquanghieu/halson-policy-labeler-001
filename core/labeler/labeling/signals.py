"""Cheap deterministic extractors run before the model sees a posting.

These are hints, not decisions: they point the model at spans worth reading
(each hint carries the exact text it matched) and feed the debug trace. None of
them names a policy section, so editing the policy never requires editing this.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from labeler.labeling.schemas import TEXT_FIELDS, Posting

_PATTERNS: dict[str, re.Pattern[str]] = {
    # EMSCAD anonymises contact details into #EMAIL_<hash>#, #PHONE_<hash>#, #URL_<hash>#.
    "contact_email": re.compile(r"#EMAIL_[0-9a-f]+#|\b[\w.+-]+@[\w-]+\.[\w.]+\b", re.I),
    "contact_phone": re.compile(r"#PHONE_[0-9a-f]+#", re.I),
    "messaging_app": re.compile(r"\b(whats\s?app|telegram|skype|signal app|wechat|viber|hangouts?|kik)\b", re.I),
    "payment_request": re.compile(
        r"\b(registration fee|application fee|starter kit|training fee|pay (?:for|a fee)|"
        r"purchase (?:your|the) (?:kit|equipment)|refundable deposit|processing fee)\b",
        re.I,
    ),
    "sensitive_data": re.compile(
        r"\b(bank (?:account|details)|routing number|social security|ssn|copy of (?:your )?(?:id|passport|driver'?s licen[cs]e)|"
        r"credit card (?:number|details))\b",
        re.I,
    ),
    "money_movement": re.compile(
        r"\b(wire transfers?|money orders?|western union|moneygram|gift cards?|bitcoin|crypto(?:currency)?|"
        r"reship\w*|forward(?:ing)? (?:packages|payments|funds)|payment processing|package inspector)\b",
        re.I,
    ),
    "high_pay_claim": re.compile(
        r"(\$\s?\d[\d,]{2,}\s*(?:\+\s*)?(?:per|a|/)\s*(?:week|day|wk)|earn up to|unlimited (?:income|earning)|"
        r"no experience (?:needed|necessary|required))",
        re.I,
    ),
}


@dataclass(frozen=True)
class Signal:
    kind: str
    field: str
    text: str


def extract_signals(posting: Posting, max_per_kind: int = 3) -> list[Signal]:
    found: list[Signal] = []
    for kind, pattern in _PATTERNS.items():
        hits = 0
        for field in TEXT_FIELDS:
            for m in pattern.finditer(getattr(posting, field) or ""):
                found.append(Signal(kind, field, m.group(0)))
                hits += 1
                if hits >= max_per_kind:
                    break
            if hits >= max_per_kind:
                break
    return found


def metadata_flags(posting: Posting) -> dict[str, bool]:
    """Missing-field facts. The policy calls these weak signals; the model is told so."""
    return {
        "no_company_profile": not posting.company_profile.strip(),
        "no_company_logo": not posting.has_company_logo,
        "no_salary": not posting.salary_range.strip(),
        "no_screening_questions": not posting.has_questions,
    }


def signals_as_dicts(signals: list[Signal]) -> list[dict[str, str]]:
    return [asdict(s) for s in signals]

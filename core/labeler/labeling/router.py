"""Cheap fraud-risk score used to route work, never to label.

A TF-IDF + logistic regression model trained on 1,000 development rows, 500 per class
(scripts/data.py; the fixed eval set is never used; RESULTS.md §7 compares it with
training on all 17,480 dev rows). Its score decides whether a
tier-1 ALLOWED is suspicious enough to escalate; the label itself always comes
from the policy-reading agent.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import joblib

from labeler.labeling.schemas import Posting


def router_text(p: Posting) -> str:
    meta = [
        f"loc_{p.location.split(',')[0].strip() or 'none'}",
        f"industry_{p.industry or 'none'}",
        f"function_{p.function or 'none'}",
        f"emp_{p.employment_type or 'none'}",
        "logo_yes" if p.has_company_logo else "logo_no",
        "questions_yes" if p.has_questions else "questions_no",
        "profile_none" if not p.company_profile.strip() else "profile_yes",
        "salary_yes" if p.salary_range else "salary_no",
    ]
    return " ".join([p.title, p.company_profile, p.description, p.requirements, p.benefits, *meta])


class Router:
    def __init__(self, pipeline):
        self.pipeline = pipeline

    def score(self, posting: Posting) -> float:
        return float(self.pipeline.predict_proba([router_text(posting)])[0][1])


@lru_cache(maxsize=4)
def load_router(path: str) -> Router | None:
    if not Path(path).exists():
        return None
    return Router(joblib.load(path))

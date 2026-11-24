"""Shapes that cross module or API boundaries."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

TEXT_FIELDS = ("title", "company_profile", "description", "requirements", "benefits")
META_FIELDS = (
    "location",
    "department",
    "salary_range",
    "employment_type",
    "required_experience",
    "required_education",
    "industry",
    "function",
    "telecommuting",
    "has_company_logo",
    "has_questions",
)


class Label(str, Enum):
    VIOLATING = "VIOLATING"
    ALLOWED = "ALLOWED"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class Posting(BaseModel):
    job_id: str
    title: str = ""
    company_profile: str = ""
    description: str = ""
    requirements: str = ""
    benefits: str = ""
    location: str = ""
    department: str = ""
    salary_range: str = ""
    employment_type: str = ""
    required_experience: str = ""
    required_education: str = ""
    industry: str = ""
    function: str = ""
    telecommuting: bool = False
    has_company_logo: bool = False
    has_questions: bool = False


class Evidence(BaseModel):
    """A verbatim quote, located in the posting so the UI can highlight it."""

    section: str
    quote: str
    field: str
    start: int
    end: int


class LabelResult(BaseModel):
    """The contract the assignment asks for, plus a debuggable trace."""

    job_id: str
    label: Label
    policy_sections: list[str]
    evidence: list[str]
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)
    policy_version: str

    evidence_spans: list[Evidence] = []
    trace: dict[str, Any] = {}

    def public(self) -> dict[str, Any]:
        """The minimal JSON shape from the brief."""
        return self.model_dump(
            include={"job_id", "label", "policy_sections", "evidence", "rationale", "confidence", "policy_version"},
            mode="json",
        )

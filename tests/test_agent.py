import asyncio

from labeler.infra.llm.fake_adapter import FakeProvider
from labeler.infra.llm.port import LLMError
from labeler.labeling.agent import LabelingAgent
from labeler.labeling.schemas import Label


def run(coro):
    return asyncio.run(coro)


def finding(section, quotes=(), facts=(), explanation="x"):
    return {"section": section, "quotes": list(quotes), "facts": list(facts), "explanation": explanation}


def reply(findings, confidence=0.9, insufficient=False, rationale="r"):
    return {
        "findings": findings,
        "insufficient_evidence": insufficient,
        "rationale": rationale,
        "confidence": confidence,
    }


def test_verified_violation(settings, policy_v1, scam_posting):
    fake = FakeProvider(
        lambda req: reply(
            [
                finding("§3", ["forward them via Western Union"]),
                finding("§2", ["email your bank account details to #EMAIL_abc123#"]),
            ]
        )
    )
    r = run(LabelingAgent(fake, settings).label(scam_posting, policy_v1))
    assert r.label is Label.VIOLATING
    assert r.policy_sections == ["§2", "§3"]  # policy order, not model order
    assert all(q in scam_posting.description for q in r.evidence)
    assert r.policy_version == "v1"
    assert r.trace["tier"] == "tier1"
    assert len(fake.calls) == 1


def test_section_with_fabricated_quote_is_dropped_and_escalated(settings, policy_v1, scam_posting):
    def handler(req):
        if req.model == "fake-small":
            return reply([finding("§1", ["pay the $99 starter kit"])])
        return reply([finding("§3", ["forward them via Western Union"])])

    fake = FakeProvider(handler)
    r = run(LabelingAgent(fake, settings).label(scam_posting, policy_v1))
    tier1 = r.trace["steps"][0]
    assert tier1["dropped_sections"] == [{"section": "§1", "reason": "no quote found in posting"}]
    assert tier1["label"] == "ALLOWED"
    assert r.trace["tier"] == "tier2"
    assert r.label is Label.VIOLATING and r.policy_sections == ["§3"]


def test_unverifiable_employer_alone_needs_review(settings, policy_v1, scam_posting):
    fake = FakeProvider(lambda req: reply([finding("§5", facts=["no_company_profile"])]))
    r = run(LabelingAgent(fake, settings).label(scam_posting, policy_v1))
    assert r.label is Label.NEEDS_REVIEW
    assert r.policy_sections == ["§5"]
    assert r.trace["section_facts"] == {"§5": ["no_company_profile"]}


def test_missing_fields_alone_never_make_a_violation(settings, policy_v1, plain_posting):
    # The model tries to call §4 on facts only; §4 is a VIOLATING section, so it is dropped.
    fake = FakeProvider(lambda req: reply([finding("§4", facts=["no_salary"])], confidence=0.95))
    settings_no_escalation = settings.__class__(**{**settings.__dict__, "tier2_model": ""})
    r = run(LabelingAgent(fake, settings_no_escalation).label(plain_posting, policy_v1))
    assert r.label is Label.ALLOWED
    assert r.policy_sections == []


def test_facts_must_be_true(settings, policy_v1, plain_posting):
    # plain_posting has a company profile, so claiming it is missing is not accepted.
    fake = FakeProvider(lambda req: reply([finding("§5", facts=["no_company_profile"])]))
    r = run(LabelingAgent(fake, settings).label(plain_posting, policy_v1))
    assert r.trace["steps"][0]["dropped_sections"][0]["section"] == "§5"


def test_low_confidence_escalates(settings, policy_v1, plain_posting):
    fake = FakeProvider(lambda req: reply([], confidence=0.4 if req.model == "fake-small" else 0.9))
    r = run(LabelingAgent(fake, settings).label(plain_posting, policy_v1))
    assert [c.model for c in fake.calls] == ["fake-small", "fake-large"]
    assert "confidence" in r.trace["escalation_reasons"][0]
    assert r.confidence == 0.9


def test_llm_failure_becomes_needs_review_not_an_exception(settings, policy_v1, plain_posting):
    class Broken:
        name = "broken"

        async def complete(self, req):
            raise LLMError("upstream down", retryable=False)

    r = run(LabelingAgent(Broken(), settings).label(plain_posting, policy_v1))
    assert r.label is Label.NEEDS_REVIEW
    assert r.confidence == 0.0
    assert r.trace["tier"] == "failed"


def test_prompt_and_schema_come_from_the_policy(settings, policy_v1, plain_posting):
    fake = FakeProvider()
    run(LabelingAgent(fake, settings).label(plain_posting, policy_v1))
    req = fake.calls[0]
    assert policy_v1.sections[0].text in req.system
    assert req.schema["properties"]["findings"]["items"]["properties"]["section"]["enum"] == policy_v1.section_ids


def test_quote_cited_by_two_sections_appears_once_in_evidence(settings, policy_v1, scam_posting):
    quote = "forward them via Western Union"
    provider = FakeProvider(
        lambda req: reply(
            [
                {"section": "§3", "quotes": [quote], "facts": [], "explanation": "x"},
                {"section": "§5", "quotes": [quote], "facts": ["no_company_profile"], "explanation": "x"},
            ]
        )
    )
    result = run(LabelingAgent(provider, settings).label(scam_posting, policy_v1))
    assert result.evidence == [quote]
    assert {e.section for e in result.evidence_spans} == {"§3", "§5"}  # spans keep the per-section detail


def _policy(name):
    from labeler.config import REPO_ROOT
    from labeler.labeling.policy import parse_policy

    return parse_policy((REPO_ROOT / "policies" / f"{name}.yaml").read_text())


def _facts_only_reply(req):
    # The reviewer's case: two review-only sections, each carried by missing fields only.
    return reply([finding("§5", facts=["no_company_profile"]), finding("§6", facts=["no_salary", "no_company_logo"])])


def test_facts_alone_never_make_violating_even_when_two_sections_combine(settings, scam_posting):
    """Edge case: missing fields must not be the sole basis for VIOLATING, whatever the count rule says.

    Checked on a policy that predates `accepts_facts` (any fact counts), so only the quote rule stands.
    """
    legacy = _policy("v2").model_copy(deep=True)
    for s in legacy.sections:
        s.accepts_facts = None
    no_escalation = settings.__class__(**{**settings.__dict__, "tier2_model": ""})
    r = run(LabelingAgent(FakeProvider(_facts_only_reply), no_escalation).label(scam_posting, legacy))
    step = r.trace["steps"][0]
    assert r.policy_sections == ["§5", "§6"] and r.evidence == []
    assert r.label is Label.NEEDS_REVIEW  # 2 sections would be VIOLATING by count; no quote, so no
    assert "no quote was verified" in step["downgraded"]


def test_downgrade_escalates_and_a_quote_restores_violating(settings, scam_posting):
    r = run(LabelingAgent(FakeProvider(_facts_only_reply), settings).label(scam_posting, _policy("v3")))
    assert r.trace["tier"] == "tier2"  # tier 2 is asked (same scripted reply here)

    def with_quote(req):
        return reply([finding("§5", facts=["no_company_profile"]), finding("§6", ["Work from home!"])])

    r = run(LabelingAgent(FakeProvider(with_quote), settings).label(scam_posting, _policy("v2")))
    assert r.label is Label.VIOLATING and r.evidence == ["Work from home!"]


def test_a_section_only_takes_the_facts_its_policy_lists(settings, scam_posting):
    no_escalation = settings.__class__(**{**settings.__dict__, "tier2_model": ""})
    r = run(LabelingAgent(FakeProvider(_facts_only_reply), no_escalation).label(scam_posting, _policy("v2")))
    step = r.trace["steps"][0]
    assert r.policy_sections == ["§5"] and r.label is Label.NEEDS_REVIEW
    assert step["dropped_sections"] == [
        {"section": "§6", "reason": "the facts given do not support this section on their own"}
    ]


def test_policy_rejects_facts_on_a_violating_section_and_unknown_facts():
    import pytest

    from labeler.labeling.policy import PolicyError, parse_policy

    src = _policy("v2").model_dump(mode="json")
    import yaml

    bad = {**src, "sections": [{**src["sections"][0], "accepts_facts": ["no_salary"]}]}
    with pytest.raises(PolicyError, match="cannot rest on facts"):
        parse_policy(yaml.safe_dump(bad, allow_unicode=True))
    bad = {**src, "sections": [{**src["sections"][4], "accepts_facts": ["no_vibes"]}]}
    with pytest.raises(PolicyError, match="unknown facts"):
        parse_policy(yaml.safe_dump(bad, allow_unicode=True))


def test_two_findings_for_one_section_keep_both_quotes(settings, policy_v1, scam_posting):
    fake = FakeProvider(
        lambda req: reply(
            [
                finding("§3", ["forward them via Western Union"]),
                finding("§3", ["receive client payments into your account"]),
            ]
        )
    )
    r = run(LabelingAgent(fake, settings).label(scam_posting, policy_v1))
    assert r.evidence == ["forward them via Western Union", "receive client payments into your account"]


import pytest  # noqa: E402


@pytest.mark.parametrize(
    "bad",
    [
        {"findings": [{"section": "§3", "quotes": None, "facts": [], "explanation": "x"}], "confidence": 0.9},
        {"findings": None, "confidence": 0.9},
        {"findings": [], "confidence": "high"},
        ["not", "an", "object"],
    ],
)
def test_malformed_model_output_is_retried_then_becomes_a_failed_label(settings, policy_v1, scam_posting, bad):
    """Structured output is not guaranteed by every provider: a bad shape must not crash the worker."""
    fake = FakeProvider(lambda req: bad)
    r = run(LabelingAgent(fake, settings).label(scam_posting, policy_v1))
    assert r.label is Label.NEEDS_REVIEW and r.trace["tier"] == "failed"
    assert "malformed model output" in r.trace["steps"][0]["error"]
    assert r.trace["steps"][0]["raw"] == bad  # the analyst sees what the model actually returned
    assert len(fake.calls) == 2  # one in-process retry, then a label (no queue redelivery)


def test_a_slow_provider_hits_the_per_posting_deadline(settings, policy_v1, plain_posting):
    import asyncio as aio

    from labeler.infra.llm.port import LLMResponse, Usage

    class Slow:
        name = "slow"

        async def complete(self, req):
            await aio.sleep(5)
            return LLMResponse(data={}, model=req.model, usage=Usage(), latency_ms=0)

    s = settings.__class__(**{**settings.__dict__, "label_deadline_s": 0.2})
    r = run(LabelingAgent(Slow(), s).label(plain_posting, policy_v1))
    assert r.trace["tier"] == "failed" and "deadline" in r.trace["steps"][0]["error"]
    assert r.trace["latency_ms"] < 2000


def test_tier2_failure_or_deadline_keeps_the_tier1_answer(settings, policy_v1, scam_posting):
    def handler(req):
        if req.model == "fake-small":
            return reply([finding("§3", ["forward them via Western Union"])], confidence=0.3)
        return {"findings": "garbage"}

    r = run(LabelingAgent(FakeProvider(handler), settings).label(scam_posting, policy_v1))
    assert r.label is Label.VIOLATING and r.trace["tier"] == "tier1"
    assert "malformed model output" in r.trace["steps"][1]["error"]


def test_trace_keeps_the_models_own_output_not_the_normalised_copy(settings, policy_v1, scam_posting):
    own = {**reply([finding("§3", ["forward them via Western Union"])]), "note_from_model": "extra key"}
    r = run(LabelingAgent(FakeProvider(lambda req: own), settings).label(scam_posting, policy_v1))
    assert r.trace["steps"][0]["raw"] == own and r.label is Label.VIOLATING


def test_labeling_deadline_must_fit_inside_the_queue_visibility_timeout():
    from pydantic import ValidationError

    from labeler.config import Settings

    with pytest.raises(ValidationError, match="must be at most queue_visibility_timeout_s"):
        Settings(_env_file=None, label_deadline_s=290, queue_visibility_timeout_s=300)
    assert Settings(_env_file=None, label_deadline_s=500, queue_visibility_timeout_s=600).label_deadline_s == 500


def test_cost_from_cli_token_counts_is_marked_as_such(settings, policy_v1, plain_posting):
    via_cli = settings.__class__(**{**settings.__dict__, "tier1_provider": "claude_cli", "tier2_model": ""})
    r = run(LabelingAgent(FakeProvider(lambda req: reply([])), via_cli).label(plain_posting, policy_v1))
    assert r.trace["cost_basis"] == "claude_cli"
    r = run(LabelingAgent(FakeProvider(lambda req: reply([])), settings).label(plain_posting, policy_v1))
    assert r.trace["cost_basis"] == "api"

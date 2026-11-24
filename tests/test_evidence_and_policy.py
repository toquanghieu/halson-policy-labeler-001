import pytest

from labeler.config import REPO_ROOT
from labeler.labeling.evidence import locate, verify
from labeler.labeling.policy import PolicyError, parse_policy
from labeler.labeling.schemas import Label, Posting


def test_exact_quote_is_located_with_offsets(scam_posting):
    ev = locate(scam_posting, "forward them via Western Union", "§3")
    assert ev is not None
    assert ev.field == "description"
    assert scam_posting.description[ev.start : ev.end] == "forward them via Western Union"


def test_quote_matches_despite_case_whitespace_and_curly_quotes():
    p = Posting(job_id="x", description="Send a copy of your ID  to\nour ‘HR’ team.")
    ev = locate(p, "send a copy of your id to our 'HR' team", "§2")
    assert ev is not None
    # The returned quote is cut from the original text, not the model's version.
    assert ev.quote == "Send a copy of your ID  to\nour ‘HR’ team"


def test_quote_matches_across_glued_sentences():
    p = Posting(job_id="x", description="We pay weekly.Please send your SSN to apply.")
    assert locate(p, "We pay weekly. Please send your SSN", "§2") is not None


def test_fabricated_or_paraphrased_quotes_are_rejected(scam_posting):
    ok, bad = verify(scam_posting, "§1", ["pay a $99 starter kit fee", "Earn $3,000 per week ... no experience"])
    assert ok == []
    assert len(bad) == 2


def test_too_short_quote_rejected(scam_posting):
    assert locate(scam_posting, "Work", "§4") is None


def test_salary_field_is_searchable():
    p = Posting(job_id="x", salary_range="5000-6000 per week")
    assert locate(p, "5000-6000 per week", "§4").field == "salary_range"


def test_aggregation_follows_section_effects(policy_v1):
    assert policy_v1.aggregate([], False) is Label.ALLOWED
    assert policy_v1.aggregate([], True) is Label.NEEDS_REVIEW
    assert policy_v1.aggregate(["§5"], False) is Label.NEEDS_REVIEW
    assert policy_v1.aggregate(["§5", "§2"], False) is Label.VIOLATING
    assert policy_v1.aggregate(["§4"], False) is Label.VIOLATING
    assert policy_v1.aggregate(["§9"], False) is Label.ALLOWED  # unknown ids are ignored


def test_two_review_only_sections_combine_into_a_violation():
    p = parse_policy(
        "version: t\ntitle: t\nsections:\n"
        "  - {id: a, name: A, effect: review_only, text: x}\n"
        "  - {id: b, name: B, effect: review_only, text: y}\n"
    )
    assert p.aggregate(["a"], False) is Label.NEEDS_REVIEW
    assert p.aggregate(["a", "b"], False) is Label.VIOLATING


def test_rendered_policy_contains_every_section(policy_v1):
    text = policy_v1.render()
    for s in policy_v1.sections:
        assert s.id in text and s.name in text
    assert "Remote work alone is not a signal." in text


@pytest.mark.parametrize(
    "source, message",
    [
        ("version: v9\ntitle: t\nsections: []", "at least 1"),
        ("version: v9\ntitle: t\nsections:\n  - {id: a, name: n, effect: maybe, text: t}", "effect"),
        ("version: 'bad version!'\ntitle: t\nsections:\n  - {id: a, name: n, effect: violating, text: t}", "version"),
        (
            "version: v9\ntitle: t\nsections:\n  - {id: a, name: n, effect: violating, text: t}\n"
            "  - {id: a, name: m, effect: violating, text: u}",
            "duplicate",
        ),
        ("sections: [unclosed", "YAML"),
    ],
)
def test_invalid_policies_are_rejected_with_a_reason(source, message):
    with pytest.raises(PolicyError, match=message):
        parse_policy(source)


def test_html_entities_match_decoded_quotes_and_offsets_cover_the_entity():
    p = Posting(job_id="e", description="We help teachers get safe &amp; secure jobs abroad &amp;")
    ev = locate(p, "We help teachers get safe & secure jobs abroad &", "§5")
    assert ev is not None
    assert ev.quote == "We help teachers get safe &amp; secure jobs abroad &amp;"  # cut from the original
    assert p.description[ev.start : ev.end] == ev.quote


def test_quotes_of_prompt_scaffolding_are_rejected():
    p = Posting(job_id="s", title="Graphic Designer", location="US, TX, Garland")
    assert locate(p, "[title]\nGraphic Designer", "§5") is None
    assert locate(p, "location: US, TX, Garland", "§5") is None


def test_combination_rule_comes_from_the_document():
    from labeler.labeling.policy import parse_policy

    src = (REPO_ROOT / "policies" / "v2.yaml").read_text()  # §5 and §6 are both review-only
    two = parse_policy(src)
    assert two.combine_to_violating == 2 and two.aggregate(["§5", "§6"], False).value == "VIOLATING"
    assert "2 or more distinct matched sections" in two.render()
    off = parse_policy(src.replace("combine_to_violating: 2", "combine_to_violating: null"))
    assert off.aggregate(["§5", "§6"], False).value == "NEEDS_REVIEW"
    assert off.aggregate(["§1", "§5"], False).value == "VIOLATING"  # a violating section still is
    assert "How sections combine" not in off.render()

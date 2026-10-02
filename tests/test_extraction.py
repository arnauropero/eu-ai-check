"""Phase 2 extraction tests (PROJECT_PLAN.md §6, Phase 2; SYSTEM_SPEC.md §3, §18).

Everything here runs offline against a fake Anthropic client: these tests pin the
fact/conclusion boundary, the fail-closed behaviour and the request shape. How
*well* the model extracts is measured by `eu-ai-check eval-extraction`, never
asserted here.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from eu_ai_check.extraction.evaluation import (
    ALL_SCORED,
    CONCEPT_FIELDS,
    concept_match,
    load_cases,
    run_arm,
    score_field,
)
from eu_ai_check.extraction.extractor import FALLBACK_BETA, MODEL, Extractor
from eu_ai_check.extraction.leakage import find_leaks
from eu_ai_check.extraction.schema import (
    ALL_FIELDS,
    BOOL_FIELDS,
    EXCLUDED_FIELDS,
    Case,
    CaseFacts,
    DateBasis,
    output_schema,
)

CASES = Path(__file__).resolve().parents[1] / "data/eval/extraction_cases.json"
TODAY = date(2026, 10, 2)

GOOD = {
    "intended_purpose": "screening job applicants",
    "ai_functionality": "ranks CVs against job descriptions",
    "relevant_date": None,
    "role_in_decision_making": "shortlists; recruiters decide",
    "develops_system": False,
    "uses_system": True,
    "puts_own_name_or_trademark": False,
    "modifies_intended_purpose": False,
    "modifies_safety_relevant_components": False,
    "jurisdiction": "Germany",
    "affected_persons": "job applicants",
}


class FakeMessages:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        out = self.outputs.pop(0) if len(self.outputs) > 1 else self.outputs[0]
        if isinstance(out, SimpleNamespace):
            return out
        return response(json.dumps(out))


def response(text, stop_reason="end_turn", model=MODEL, iterations=None, stop_details=None):
    return SimpleNamespace(
        model=model,
        stop_reason=stop_reason,
        stop_details=stop_details,
        content=[SimpleNamespace(type="text", text=text)] if text is not None else [],
        usage=SimpleNamespace(input_tokens=100, output_tokens=50, iterations=iterations),
    )


def fake_client(*outputs):
    messages = FakeMessages(outputs)
    return SimpleNamespace(beta=SimpleNamespace(messages=messages), messages=messages)


# --------------------------------------------------------------------------
# Schema: the boundary is structural
# --------------------------------------------------------------------------

def test_schema_has_no_conclusion_field() -> None:
    assert not EXCLUDED_FIELDS & set(output_schema()["properties"])
    assert not EXCLUDED_FIELDS & set(ALL_FIELDS)


def test_schema_is_closed_and_fully_required() -> None:
    schema = output_schema()
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"]) == set(ALL_FIELDS)


def test_only_the_two_descriptors_are_non_nullable() -> None:
    nullable = {
        name for name, prop in output_schema()["properties"].items()
        if any(alt.get("type") == "null" for alt in prop.get("anyOf", []))
    }
    assert set(ALL_FIELDS) - nullable == {"intended_purpose", "ai_functionality"}


def test_model_rejects_a_conclusion_field() -> None:
    with pytest.raises(ValueError):
        CaseFacts.model_validate({**GOOD, "risk_tier": "high_risk"})


def test_missing_date_defaults_explicitly_to_assessment_date() -> None:
    facts = CaseFacts.model_validate(GOOD)
    case = Case.from_facts(facts, "text", TODAY)
    assert case.relevant_date == TODAY
    assert case.relevant_date_basis == DateBasis.ASSESSMENT_DATE
    stated = Case.from_facts(facts.model_copy(update={"relevant_date": date(2027, 3, 1)}), "t", TODAY)
    assert stated.relevant_date_basis == DateBasis.STATED


# --------------------------------------------------------------------------
# Leakage (zero tolerance)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("value", [
    "a high-risk recruitment system",
    "high risk credit scoring",
    "used by the deployer team",
    "we are the provider of the AI system",
    "acting as provider",
    "after a substantial modification",
    "covered by Annex III",
    "per Article 16",
    "a prohibited AI practice",
    "minimal risk chatbot",
])
def test_conclusion_terms_are_caught(value: str) -> None:
    assert find_leaks({**GOOD, "intended_purpose": value})


@pytest.mark.parametrize("value", [
    "healthcare provider triage",
    "tool supplied by the payroll provider",
    "detects burst risk in water pipes",
    "deploys ambulances",
    "risk of reoffending",
])
def test_ordinary_words_are_not_leaks(value: str) -> None:
    assert find_leaks({**GOOD, "intended_purpose": value}) == []


def test_excluded_field_in_raw_output_is_a_leak() -> None:
    leaks = find_leaks({**GOOD, "actor_roles": ["provider"]})
    assert [l.kind for l in leaks] == ["excluded_field"]


# --------------------------------------------------------------------------
# Extractor: request shape and fail-closed handling
# --------------------------------------------------------------------------

def test_request_uses_structured_output_fallbacks_and_explicit_effort() -> None:
    req = Extractor(fake_client(GOOD), effort="low").request("text", TODAY)
    assert req["model"] == "claude-opus-5-5"
    assert req["output_config"]["effort"] == "low"
    assert req["output_config"]["format"]["schema"] == output_schema()
    assert req["betas"] == [FALLBACK_BETA] and req["fallbacks"] == "default"
    assert "thinking" not in req  # always on for Opus 5.5; disabling it is a 400
    assert "Assessment date: 2026-10-02" in req["messages"][0]["content"]
    assert "FIELD RULES" in req["system"] and "{rules}" not in req["system"]


def test_good_output_validates() -> None:
    result = Extractor(fake_client(GOOD)).extract("text", TODAY)
    assert result.ok and result.facts.jurisdiction == "Germany" and not result.leaks


def test_refusal_fails_closed() -> None:
    client = fake_client(response(None, stop_reason="refusal",
                                  stop_details=SimpleNamespace(category="cyber")))
    result = Extractor(client).extract("text", TODAY)
    assert result.facts is None and "refusal" in result.error


def test_truncation_fails_closed() -> None:
    result = Extractor(fake_client(response('{"intended_', stop_reason="max_tokens"))).extract("t", TODAY)
    assert result.facts is None and "max_tokens" in result.error


def test_invalid_json_fails_closed() -> None:
    result = Extractor(fake_client(response("not json"))).extract("t", TODAY)
    assert result.facts is None and "invalid JSON" in result.error


def test_excluded_field_is_both_a_leak_and_a_failure() -> None:
    result = Extractor(fake_client({**GOOD, "risk_tier": "high_risk"})).extract("t", TODAY)
    assert result.facts is None
    assert [l.kind for l in result.leaks] == ["excluded_field"]


def test_fallback_is_recorded() -> None:
    client = fake_client(response(json.dumps(GOOD), model="claude-opus-4-8",
                                  iterations=[SimpleNamespace(type="message"),
                                              SimpleNamespace(type="fallback_message")]))
    result = Extractor(client).extract("t", TODAY)
    assert result.ok and result.fell_back and result.served_model == "claude-opus-4-8"


# --------------------------------------------------------------------------
# Eval set integrity (checked before any paid run)
# --------------------------------------------------------------------------

def test_eval_cases_are_well_formed() -> None:
    cases = load_cases(CASES)
    assert len({c.id for c in cases}) == len(cases) >= 30
    for c in cases:
        assert set(c.expected) == set(ALL_SCORED) == set(ALL_FIELDS), c.id
        for f in BOOL_FIELDS:
            assert c.expected[f] in (True, False, None), (c.id, f)
        if c.expected["relevant_date"] is not None:
            date.fromisoformat(c.expected["relevant_date"])
        for f in CONCEPT_FIELDS:
            label = c.expected[f]
            if f in ("intended_purpose", "ai_functionality"):
                assert label is not None, (c.id, f)  # non-nullable in the schema
            if label is not None:
                assert label["concepts"] and all(label["concepts"]), (c.id, f)
                assert label["reference"], (c.id, f)


def test_every_reference_satisfies_its_own_concept_groups() -> None:
    """Label sanity: if a label's own reference sentence fails its concept
    groups, the label (not the model) is wrong."""
    for c in load_cases(CASES):
        for f in CONCEPT_FIELDS:
            label = c.expected[f]
            if label is not None:
                assert concept_match(label["reference"], label), (c.id, f, label["reference"])


def test_every_category_is_declared() -> None:
    declared = set(json.loads(CASES.read_text(encoding="utf-8"))["categories"])
    assert {c.category for c in load_cases(CASES)} <= declared


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------

def test_concept_match() -> None:
    label = {"concepts": [["scor", "rank"], ["cv", "resume"]], "reference": "x"}
    assert concept_match("Ranks CVs", label)
    assert not concept_match("ranks candidates", label)
    assert not concept_match(None, label)
    assert concept_match(None, None) and not concept_match("anything", None)


def test_dates_and_null_booleans_score_exactly() -> None:
    assert score_field("relevant_date", date(2027, 3, 1), "2027-03-01")
    assert not score_field("relevant_date", None, "2027-03-01")
    assert not score_field("develops_system", False, None)  # null is not false
    assert score_field("develops_system", None, None)


def test_harness_scores_a_perfect_extractor_as_perfect() -> None:
    """An extractor that returns each label's reference (and exact booleans /
    dates) must score 1.0 -- otherwise the harness, not the model, is broken."""
    cases = load_cases(CASES)

    class Perfect:
        def extract(self, description, assessment_date):
            from eu_ai_check.extraction.extractor import ExtractionResult
            case = next(c for c in cases if c.description == description)
            raw = {f: (v["reference"] if isinstance(v, dict) else v) for f, v in case.expected.items()}
            return ExtractionResult(facts=CaseFacts.model_validate(raw), raw=raw)

    arm = run_arm("perfect", Perfect(), cases, repeats=1, workers=1)
    assert arm.overall_accuracy() == 1.0
    assert arm.leaks() == [] and arm.unstable() == []


# --------------------------------------------------------------------------
# The documented labelling rules match the prompt and the labels
# --------------------------------------------------------------------------

def _rules() -> dict:
    return json.loads(CASES.read_text(encoding="utf-8"))["labelling_rules"]


def _expected() -> dict[str, dict]:
    return {c.id: c.expected for c in load_cases(CASES)}


def test_eval_rules_are_the_prompt_rules_verbatim() -> None:
    """The model is held to exactly the definitions the labels were written to."""
    from eu_ai_check.extraction.extractor import FIELD_RULES
    assert "\n".join(_rules()["field_rules_verbatim"]) == FIELD_RULES


def _decision(rule_id: str) -> dict:
    return next(d for d in _rules()["labelling_decisions"] if d["id"] == rule_id)


def test_branding_rule_l3_matches_labels() -> None:
    exp, l3 = _expected(), _decision("L3")
    for key, value in (("cases_false", False), ("cases_null", None), ("cases_true", True)):
        for cid in l3[key]:
            assert exp[cid]["puts_own_name_or_trademark"] is value, cid
    assert exp["E18"]["puts_own_name_or_trademark"] is None  # reviewed 2026-10-02


def test_null_discipline_rule_l8_lists_exactly_the_null_text_labels() -> None:
    exp = _expected()
    actual = sorted(
        cid for cid, e in exp.items()
        if e["role_in_decision_making"] is None or e["affected_persons"] is None
    )
    assert actual == sorted(_decision("L8")["cases_null"])


def test_actor_rules_match_labels() -> None:
    exp = _expected()
    for cid in _decision("L1")["cases"]:
        assert exp[cid]["develops_system"] is False and exp[cid]["modifies_safety_relevant_components"] is True
    assert exp["E25"]["develops_system"] is False  # L2: sister company
    for cid in _decision("L4")["cases_planned_use"]:
        assert exp[cid]["uses_system"] is True
    for cid in _decision("L4")["cases_supplier_only"]:
        assert exp[cid]["uses_system"] is False
    for cid in _decision("L9")["cases"]:
        assert all(exp[cid][f] is None for f in BOOL_FIELDS), cid

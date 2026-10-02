"""Phase 2 extraction on OpenAI GPT-6.1 Sol -- offline tests with a fake client.

These pin the provider switch (2026-10-02): the request shape, the exact model and
reasoning-effort settings, the strict Structured Outputs schema and its equivalence
to the pre-registered `CaseFacts` schema, fail-closed handling, per-result
recording, and -- most importantly -- that the pre-registered evaluation itself did
not change.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from eu_ai_check.extraction.evaluation import JUDGE_MODEL, load_cases, run_arm, run_records
from eu_ai_check.extraction.extractor import FIELD_RULES, system_prompt, user_message
from eu_ai_check.extraction.openai_extractor import (
    EFFORT_MAP,
    OPENAI_MODEL,
    SCHEMA_NAME,
    OpenAIExtractor,
)
from eu_ai_check.extraction.providers import DEFAULT_PROVIDER, MODELS, create_extractor
from eu_ai_check.extraction.schema import ALL_FIELDS, openai_strict_schema, output_schema

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "data/eval/extraction_cases.json"
TODAY = date(2026, 10, 2)

GOOD = {
    "intended_purpose": "screening job applicants",
    "ai_functionality": "ranks CVs against job descriptions",
    "relevant_date": "2027-03-01",
    "role_in_decision_making": "shortlists; recruiters decide",
    "develops_system": False,
    "uses_system": True,
    "puts_own_name_or_trademark": False,
    "modifies_intended_purpose": False,
    "modifies_safety_relevant_components": False,
    "jurisdiction": "Germany",
    "affected_persons": "job applicants",
}


def openai_response(text=None, *, refusal=None, status="completed", reason=None,
                    model="gpt-6.1-sol"):
    content = []
    if refusal is not None:
        content.append(SimpleNamespace(type="refusal", refusal=refusal))
    if text is not None:
        content.append(SimpleNamespace(type="output_text", text=text))
    return SimpleNamespace(
        model=model,
        status=status,
        incomplete_details=SimpleNamespace(reason=reason) if reason else None,
        output=[SimpleNamespace(type="reasoning"), SimpleNamespace(type="message", content=content)],
        usage=SimpleNamespace(input_tokens=1200, output_tokens=300),
    )


class FakeResponses:
    def __init__(self, response):
        self.response = response
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def fake_openai(response):
    return SimpleNamespace(responses=FakeResponses(response))


def extract_with(response, effort="medium"):
    client = fake_openai(response)
    return OpenAIExtractor(client, effort=effort).extract("description", TODAY), client


# --------------------------------------------------------------------------
# Model and reasoning-effort configuration
# --------------------------------------------------------------------------

def test_phase_2_extraction_model_is_gpt_6_1_sol() -> None:
    assert OPENAI_MODEL == "gpt-6.1-sol"
    assert DEFAULT_PROVIDER == "openai"
    assert MODELS["openai"] == "gpt-6.1-sol"
    assert isinstance(create_extractor(client=fake_openai(None)), OpenAIExtractor)


def test_pre_registered_effort_arms_map_exactly() -> None:
    """GPT-6.1 Sol supports low and medium natively: identity mapping."""
    assert EFFORT_MAP == {"low": "low", "medium": "medium"}


@pytest.mark.parametrize("effort", ["low", "medium"])
def test_request_shape(effort: str) -> None:
    req = OpenAIExtractor(fake_openai(None), effort=effort).request("some text", TODAY)
    assert req["model"] == "gpt-6.1-sol"
    assert req["reasoning"] == {"effort": effort}
    fmt = req["text"]["format"]
    assert fmt == {"type": "json_schema", "name": SCHEMA_NAME,
                   "schema": openai_strict_schema(), "strict": True}
    system, user = req["input"]
    assert system == {"role": "system", "content": system_prompt()}
    assert FIELD_RULES in system["content"]  # same rules the labels were written to
    assert user == {"role": "user", "content": user_message("some text", TODAY)}
    assert "temperature" not in req


@pytest.mark.parametrize("effort", ["none", "minimal", "high", "xhigh"])
def test_efforts_outside_the_pre_registered_arms_are_rejected(effort: str) -> None:
    with pytest.raises(ValueError):
        OpenAIExtractor(fake_openai(None), effort=effort)


# --------------------------------------------------------------------------
# Structured Outputs schema == the pre-registered CaseFacts schema
# --------------------------------------------------------------------------

def _nullable_and_type(prop: dict) -> tuple[bool, str]:
    if "anyOf" in prop:
        types = [a["type"] for a in prop["anyOf"]]
    elif isinstance(prop["type"], list):
        types = prop["type"]
    else:
        types = [prop["type"]]
    return "null" in types, next(t for t in types if t != "null")


def test_openai_schema_is_strict_compatible() -> None:
    schema = openai_strict_schema()
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert schema["required"] == list(schema["properties"])
    text = json.dumps(schema)
    assert "anyOf" not in text and '"format"' not in text and "title" not in text


def test_openai_schema_matches_case_facts_field_for_field() -> None:
    """Same property set, same base types, same nullability, same descriptions:
    only the nullable notation and the dropped date `format` differ."""
    base, strict = output_schema()["properties"], openai_strict_schema()["properties"]
    assert list(strict) == list(base) == list(ALL_FIELDS)
    for name in base:
        assert _nullable_and_type(strict[name]) == _nullable_and_type(base[name]), name
        assert strict[name]["description"].startswith(base[name]["description"]), name
    assert "YYYY-MM-DD" in strict["relevant_date"]["description"]


# --------------------------------------------------------------------------
# Output handling: validate, record, fail closed
# --------------------------------------------------------------------------

def test_good_output_validates_and_records_model_and_effort() -> None:
    result, client = extract_with(openai_response(json.dumps(GOOD), model="gpt-6.1-sol-2026-09-15"),
                                  effort="low")
    assert result.ok and result.facts.relevant_date == date(2027, 3, 1)
    assert (result.provider, result.requested_model, result.effort) == ("openai", "gpt-6.1-sol", "low")
    assert result.served_model == "gpt-6.1-sol-2026-09-15"  # whatever the API reports
    assert (result.input_tokens, result.output_tokens) == (1200, 300)
    assert not result.leaks


def test_refusal_fails_closed() -> None:
    result, _ = extract_with(openai_response(refusal="I can't help with that."))
    assert result.facts is None and result.error.startswith("refusal")


def test_incomplete_response_fails_closed() -> None:
    result, _ = extract_with(openai_response('{"intended_', status="incomplete",
                                             reason="max_output_tokens"))
    assert result.facts is None and "max_output_tokens" in result.error


def test_non_iso_date_fails_closed() -> None:
    """The date `format` is not sent to OpenAI; pydantic is what enforces it."""
    result, _ = extract_with(openai_response(json.dumps({**GOOD, "relevant_date": "next March"})))
    assert result.facts is None and "schema validation failed" in result.error


def test_excluded_field_is_a_leak_and_a_failure() -> None:
    result, _ = extract_with(openai_response(json.dumps({**GOOD, "actor_roles": ["deployer"]})))
    assert result.facts is None
    assert [l.kind for l in result.leaks] == ["excluded_field"]


def test_conclusion_in_a_value_is_caught_on_openai_too() -> None:
    result, _ = extract_with(openai_response(json.dumps(
        {**GOOD, "intended_purpose": "a high-risk recruitment system"})))
    assert result.ok and result.leaks  # valid schema, still a zero-tolerance leak


def test_missing_output_text_fails_closed() -> None:
    result, _ = extract_with(openai_response(None))
    assert result.facts is None and "no text output" in result.error


# --------------------------------------------------------------------------
# Every eval result records exact model and effort
# --------------------------------------------------------------------------

def test_run_records_carry_provider_model_and_effort() -> None:
    cases = load_cases(CASES)[:2]
    client = fake_openai(openai_response(json.dumps(GOOD)))
    arm = run_arm("gpt-6.1-sol effort=low", OpenAIExtractor(client, effort="low"), cases,
                  repeats=3, workers=1)
    records = run_records([arm])
    assert len(records) == 6  # 2 cases x 3 repeats
    for r in records:
        assert r["provider"] == "openai"
        assert r["requested_model"] == "gpt-6.1-sol"
        assert r["served_model"] == "gpt-6.1-sol"
        assert r["effort"] == "low"


def test_judge_methodology_unchanged() -> None:
    """The secondary judge stays Claude Opus 5.5, as pre-registered."""
    assert JUDGE_MODEL == "claude-opus-5-5"


# --------------------------------------------------------------------------
# The pre-registered evaluation is untouched by the provider switch
# --------------------------------------------------------------------------

#: Canonical SHA-256 of each part of data/eval/extraction_cases.json as committed
#: in the pre-registration, fe0b0f6 (2026-10-02). Hashing parsed JSON, not bytes,
#: keeps the check independent of line endings on checkout.
PRE_REGISTERED = {
    "cases": "b05e12f8d9088793bc12642aca739387c864e99015d8e82785bd7b86c3029fe9",
    "labelling_rules": "d2f806eecbd1eeee98d4a442962f55102291c8f297288a5f10ce58777553222c",
    "scoring": "304745eb0857501280dbfa5211aa08f4339a25eb93d4bdfbc8b2a7af9a69035c",
    "categories": "de45c83babd853fd934979622c7ba54d2956be4027c793ffb2b940ea211d8637",
}


@pytest.mark.parametrize("part", sorted(PRE_REGISTERED))
def test_pre_registered_eval_content_is_unchanged(part: str) -> None:
    data = json.loads(CASES.read_text(encoding="utf-8"))
    digest = hashlib.sha256(
        json.dumps(data[part], sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    assert digest == PRE_REGISTERED[part], (
        f"{part} differs from the pre-registration (fe0b0f6). A change after "
        "pre-registration must be flagged post_hoc, not silently absorbed."
    )


def test_still_thirty_cases_three_repeats_two_arms(monkeypatch: pytest.MonkeyPatch) -> None:
    """The eval command's defaults are the pre-registered design, now on OpenAI."""
    from eu_ai_check import cli

    assert len(load_cases(CASES)) == 30
    seen = {}
    monkeypatch.setattr(cli, "cmd_eval_extraction", lambda args: seen.update(vars(args)) or 0)
    cli.main(["eval-extraction"])
    assert seen["repeats"] == 3
    assert seen["efforts"] == "low,medium"
    assert seen["provider"] == "openai"

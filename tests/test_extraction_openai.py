"""Phase 2 LLM evaluation stack on OpenAI -- offline tests with fake clients.

These pin the switch to OpenAI (2026-10-02): extractor GPT-6.1 Sol and judge
GPT-6 Astra; the exact models and reasoning-effort settings; the strict
Structured Outputs schemas and their equivalence to the pre-registered ones;
fail-closed handling; per-result recording; that the judge never touches the
ground truth or the primary score; that the evaluation workflow needs only
OPENAI_API_KEY; and -- most importantly -- that the pre-registered evaluation
itself did not change. No test here makes a network call.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from eu_ai_check.extraction.evaluation import (
    _JUDGE_SCHEMA,
    JUDGE_EFFORT,
    JUDGE_MODEL,
    JUDGE_SYSTEM,
    judge,
    judge_request,
    load_cases,
    run_arm,
    run_records,
)
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


# --------------------------------------------------------------------------
# Judge: OpenAI, secondary, never touches the ground truth
# --------------------------------------------------------------------------

def judge_response(verdicts=None, *, refusal=None, status="completed"):
    text = None if verdicts is None else json.dumps({"verdicts": verdicts})
    return openai_response(text, refusal=refusal, status=status, model=JUDGE_MODEL)


def test_judge_is_gpt_6_astra_and_not_the_extractor() -> None:
    assert JUDGE_MODEL == "gpt-6-astra"
    assert JUDGE_EFFORT == "low"
    assert JUDGE_MODEL != OPENAI_MODEL  # no output is graded by the model that produced it


#: SHA-256 of the judge prompt and verdict schema as pre-registered (fe0b0f6).
#: Migrating the judge changed the provider and model, not the method.
PRE_REGISTERED_JUDGE_PROMPT = "1c5db014d50a24ba3bedc7a16c32392fc3a5b3a7a960b68b9ec759d42135cc7b"
PRE_REGISTERED_JUDGE_SCHEMA = "08467002c705533788a410f456983bedefed286589ee519f8290ed84c83e0518"


def test_judge_prompt_and_verdict_schema_are_unchanged() -> None:
    assert hashlib.sha256(JUDGE_SYSTEM.encode()).hexdigest() == PRE_REGISTERED_JUDGE_PROMPT
    schema_digest = hashlib.sha256(json.dumps(_JUDGE_SCHEMA, sort_keys=True).encode()).hexdigest()
    assert schema_digest == PRE_REGISTERED_JUDGE_SCHEMA


def test_judge_request_shape() -> None:
    case = load_cases(CASES)[0]
    req = judge_request(case, {"intended_purpose": "hiring warehouse staff"})
    assert req["model"] == "gpt-6-astra"
    assert req["reasoning"] == {"effort": "low"}
    assert req["text"]["format"] == {"type": "json_schema", "name": "judge_verdicts",
                                     "schema": _JUDGE_SCHEMA, "strict": True}
    system, user = req["input"]
    assert system == {"role": "system", "content": JUDGE_SYSTEM}
    assert case.expected["intended_purpose"]["reference"] in user["content"]
    assert "hiring warehouse staff" in user["content"]


def test_judge_parses_verdicts() -> None:
    case = load_cases(CASES)[0]
    client = fake_openai(judge_response([{"field": "intended_purpose", "same_fact": True, "reason": "ok"}]))
    verdicts, error = judge(client, case, {"intended_purpose": "x"})
    assert verdicts == {"intended_purpose": (True, "ok")} and error is None


@pytest.mark.parametrize("response, fragment", [
    (judge_response(refusal="no"), "refusal"),
    (judge_response(None, status="incomplete"), "unusable"),
    (openai_response("not json", model=JUDGE_MODEL), "invalid"),
])
def test_judge_failure_is_recorded_not_fatal(response, fragment) -> None:
    verdicts, error = judge(fake_openai(response), load_cases(CASES)[0], {"intended_purpose": "x"})
    assert verdicts == {} and fragment in error


class RoutingResponses:
    """One fake OpenAI client serving both roles, routed by model -- the shape of
    the real workflow, where one OPENAI_API_KEY client runs extractor and judge."""

    def __init__(self, extraction, judgement):
        self.extraction, self.judgement = extraction, judgement
        self.models: list[str] = []

    def create(self, **kwargs):
        self.models.append(kwargs["model"])
        return self.extraction if kwargs["model"] == OPENAI_MODEL else self.judgement


def test_judge_never_changes_ground_truth_or_primary_score() -> None:
    """A judge that disagrees with everything leaves labels, correctness and every
    accuracy figure exactly as without a judge."""
    cases = load_cases(CASES)
    before = json.dumps([c.expected for c in cases], sort_keys=True)
    extraction = openai_response(json.dumps(GOOD))
    hostile = judge_response([{"field": f, "same_fact": False, "reason": "disagree"}
                              for f in ("intended_purpose", "ai_functionality",
                                        "role_in_decision_making", "affected_persons")])

    plain = run_arm("plain", OpenAIExtractor(fake_openai(extraction)), cases, 1, workers=1)
    routed = SimpleNamespace(responses=RoutingResponses(extraction, hostile))
    judged = run_arm("judged", OpenAIExtractor(routed), cases, 1, judge_client=routed, workers=1)

    assert JUDGE_MODEL in routed.responses.models  # the judge really ran
    assert [r.correct for r in judged.runs] == [r.correct for r in plain.runs]
    assert judged.overall_accuracy() == plain.overall_accuracy()
    assert json.dumps([c.expected for c in cases], sort_keys=True) == before


def test_no_judge_means_only_the_extractor_is_called() -> None:
    routed = SimpleNamespace(responses=RoutingResponses(openai_response(json.dumps(GOOD)), None))
    run_arm("no judge", OpenAIExtractor(routed), load_cases(CASES)[:3], 2, judge_client=None, workers=1)
    assert set(routed.responses.models) == {OPENAI_MODEL}


# --------------------------------------------------------------------------
# The evaluation workflow needs OPENAI_API_KEY only
# --------------------------------------------------------------------------

@pytest.mark.parametrize("no_judge", [False, True])
def test_eval_workflow_runs_on_openai_alone(monkeypatch, tmp_path, no_judge) -> None:
    """Runs the real `eval-extraction` command end to end against a fake OpenAI
    client, with ANTHROPIC_API_KEY unset and the anthropic package made
    unimportable: any hidden Anthropic dependency would fail here."""
    import sys

    import openai

    from eu_ai_check import cli

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setitem(sys.modules, "anthropic", None)  # `import anthropic` now raises
    routes = RoutingResponses(
        openai_response(json.dumps(GOOD)),
        judge_response([{"field": "intended_purpose", "same_fact": True, "reason": "ok"}]),
    )
    monkeypatch.setattr(openai, "OpenAI", lambda *a, **k: SimpleNamespace(responses=routes))

    out = tmp_path / "run"
    argv = ["eval-extraction", "--repeats", "1", "--out", str(out)]
    if no_judge:
        argv.append("--no-judge")
    cli.main(argv)

    assert set(routes.models) == ({OPENAI_MODEL} if no_judge else {OPENAI_MODEL, JUDGE_MODEL})
    lines = out.with_suffix(".runs.jsonl").read_text("utf-8").splitlines()
    records = [json.loads(line) for line in lines]
    assert len(records) == 30 * 2  # 30 cases x 1 repeat x 2 effort arms
    assert {r["effort"] for r in records} == {"low", "medium"}
    assert {(r["provider"], r["requested_model"]) for r in records} == {("openai", "gpt-6.1-sol")}
    # Every case labels intended_purpose and the fake fills every text field, so
    # with the judge on, every record was judged -- by gpt-6-astra and nothing else.
    judge_models = {r["judge_model"] for r in records}
    assert judge_models == ({None} if no_judge else {"gpt-6-astra"})


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
    assert seen["no_judge"] is False  # judge on by default; --no-judge skips it


# --------------------------------------------------------------------------
# Production default = the accepted Phase 2 configuration (SYSTEM_SPEC.md §18)
# --------------------------------------------------------------------------

def test_default_production_extraction_effort_is_low() -> None:
    """Accepted configuration: gpt-6.1-sol + baseline prompt + low effort."""
    from eu_ai_check.extraction.extractor import DEFAULT_EFFORT

    assert DEFAULT_EFFORT == "low"
    client = fake_openai(openai_response(json.dumps(GOOD)))
    ex = OpenAIExtractor(client)
    assert (ex.effort, ex.prompt_variant) == ("low", "baseline")
    assert ex.request("x", TODAY)["reasoning"] == {"effort": "low"}
    assert ex.request("x", TODAY)["input"][0]["content"] == system_prompt()
    assert ex.extract("x", TODAY).effort == "low"
    made = create_extractor(client=client)
    assert isinstance(made, OpenAIExtractor) and made.effort == "low"


def _run_extract_cli(monkeypatch, argv: list[str]) -> list[dict]:
    import openai

    from eu_ai_check import cli

    responses = FakeResponses(openai_response(json.dumps(GOOD)))
    monkeypatch.setattr(openai, "OpenAI", lambda *a, **k: SimpleNamespace(responses=responses))
    cli.main(["extract", "We use an AI tool to screen CVs.", "--date", "2026-10-02", *argv])
    return responses.calls


def test_extract_cli_defaults_to_low_effort(monkeypatch, capsys) -> None:
    calls = _run_extract_cli(monkeypatch, [])
    assert [c["reasoning"] for c in calls] == [{"effort": "low"}]
    assert calls[0]["model"] == "gpt-6.1-sol"
    assert "effort=low" in capsys.readouterr().out


def test_extract_cli_explicit_medium_still_works(monkeypatch, capsys) -> None:
    calls = _run_extract_cli(monkeypatch, ["--effort", "medium"])
    assert [c["reasoning"] for c in calls] == [{"effort": "medium"}]
    assert "effort=medium" in capsys.readouterr().out

"""`eu-ai-check assess` with a fake OpenAI client only (no key, no network, no spend).
A fake run is a plumbing check, never a result."""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

import pytest

from e2e_fakes import FakeOpenAI
from eu_ai_check.e2e import assess as A
from eu_ai_check.e2e import runner
from eu_ai_check.e2e.spend import LEDGER

DESC = ("We run a regional bus company. We bought RouteWise, used as supplied, which "
        "plans the order in which buses are cleaned overnight. Which AI Act rules apply "
        "to us on 1 November 2026?")
FACTS = {"intended_purpose": "bus cleaning order", "ai_functionality": "plans an order",
         "relevant_date": "2026-11-01", "role_in_decision_making": None,
         "develops_system": False, "uses_system": True, "puts_own_name_or_trademark": False,
         "modifies_intended_purpose": False, "modifies_safety_relevant_components": False,
         "jurisdiction": None, "affected_persons": None}
ON = date(2026, 11, 4)


def dnm(case, cands):
    return [{"provision_id": p, "rationale": "fake", "verdict": "does_not_match",
             "case_quote": case["description"][:20], "provision_quote": None} for p in cands]


def fake(desc=DESC, facts=FACTS, **kw) -> FakeOpenAI:
    return FakeOpenAI([{"id": "assess", "description": desc, "gold_facts": facts}],
                      verdicts=dnm, **kw)


def stages(f) -> runner.Stages:
    return runner.Stages.frozen(runner.RecordingClient(f))


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(runner.time, "sleep", lambda s: None)
    monkeypatch.setattr(runner.gen_eval.time, "sleep", lambda s: None)


def test_assess_runs_the_full_frozen_pipeline_and_prints_cost_and_limitations() -> None:
    f = fake()
    result = A.assess(DESC, ON, stages(f))
    assert [s for s, _ in f.calls] == ["case_facts", "provision_verdicts", "generated_answer"]
    assert result["answer"] and result["fallback"] is False
    assert result["statuses"]["risk_tier"] == "not_applicable/conditions_not_met"
    assert result["relevant_date"] == "2026-11-01"
    assert result["cost_usd"] == pytest.approx(3 * (1000 * 2 + 500 * 10) / 1e6)
    text = A.render(result)
    assert text.startswith(result["answer"])
    assert f"Cost of this run: ${result['cost_usd']:.4f} (3 API calls" in text
    assert (f"worst case checked before the run ${result['worst_case_usd']:.2f}, "
            f"cap --max-cost {chr(36)}0.75)") in text
    assert text.rstrip().endswith(A.LIMITATIONS_POINTER)
    assert "Not legal advice" in text


def test_the_assessment_date_is_sent_to_extraction() -> None:
    f = fake()
    seen = []
    orig = f.create

    def spy(**req):
        seen.append(req["input"][1]["content"])
        return orig(**req)

    f.create = spy
    f.responses = f
    A.assess(DESC, date(2026, 11, 2), stages(f))
    assert seen[0].startswith("Assessment date: 2026-11-02")


def test_refused_before_any_call_if_the_worst_case_exceeds_the_cap() -> None:
    f = fake()
    with pytest.raises(A.CostCapExceeded, match="refused before any call"):
        A.assess(DESC, ON, stages(f), max_cost=0.10)
    assert f.calls == []


def test_the_default_cap_covers_the_worst_case_and_a_run_never_exceeds_the_cap() -> None:
    env = runner._Env()
    bound = A.worst_case(stages(fake()), DESC, ON, env.corpus.provisions)
    assert 0.5 < bound <= A.DEFAULT_MAX_COST_USD
    f = fake(tokens={"provision_verdicts": (8000, 32_000), "case_facts": (2000, 16_000),
                     "generated_answer": (9000, 5000)})
    result = A.assess(DESC, ON, stages(f))
    assert result["cost_usd"] <= A.DEFAULT_MAX_COST_USD


def test_a_call_that_could_breach_the_cap_is_refused_mid_run() -> None:
    f = fake(tokens={"case_facts": (1000, 60_000)})            # $0.60 extraction
    result = A.assess(DESC, ON, stages(f), max_cost=0.70)
    assert result["answer"] is None and result["stage_failed"] == "budget"
    assert "--max-cost" in result["error"]
    assert result["cost_usd"] <= 0.70
    assert ("provision_verdicts", "assess") not in f.calls


def test_a_date_before_entry_into_force_makes_no_matcher_call() -> None:
    desc = DESC.replace("1 November 2026", "1 June 2024")
    f = fake(desc, {**FACTS, "relevant_date": "2024-06-01"})
    result = A.assess(desc, ON, stages(f))
    assert [s for s, _ in f.calls] == ["case_facts", "generated_answer"]
    assert result["statuses"]["risk_tier"] == "not_in_force"


def test_a_failed_stage_prints_no_answer() -> None:
    import httpx
    import openai

    def down(stage, case, n):
        return openai.AuthenticationError("bad key", response=httpx.Response(
            401, request=httpx.Request("POST", "https://api.openai.com/v1/responses")), body=None)

    result = A.assess(DESC, ON, stages(fake(raise_on=down)))
    assert result["answer"] is None and result["stage_failed"] == "extract"
    assert A.render(result).startswith("No answer: extract: AuthenticationError")


def test_only_the_frozen_configuration_runs() -> None:
    f = fake()
    s = stages(f)
    s.extractor.effort = "medium"
    with pytest.raises(RuntimeError, match="frozen configuration"):
        A.assess(DESC, ON, s)
    assert f.calls == []


def _digest(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def test_assess_writes_nothing_and_leaves_evaluation_records_alone() -> None:
    watched = [LEDGER, runner.HELDOUT_SUMMARY, runner.HELDOUT_RUNS, runner.HELDOUT_MARKER,
               Path("data/demo/recording.json")]
    before = {p: _digest(p) for p in watched}
    A.assess(DESC, ON, stages(fake()))
    assert {p: _digest(p) for p in watched} == before
    assert runner.EVAL_CLOCK == date(2026, 10, 25)


def test_cli_parses_assess_with_a_date_and_cap() -> None:
    import argparse

    from eu_ai_check.e2e.cli import register

    parser = argparse.ArgumentParser()
    register(parser.add_subparsers(dest="cmd"))
    args = parser.parse_args(["assess", DESC, "--date", "2026-11-01", "--max-cost", "0.9"])
    assert (args.text, args.date, args.max_cost) == (DESC, date(2026, 11, 1), 0.9)
    default = parser.parse_args(["assess", DESC])
    assert default.max_cost == A.DEFAULT_MAX_COST_USD
    assert isinstance(default.date, date)


def test_without_a_key_assess_explains_what_to_do_and_makes_no_call(monkeypatch, capsys) -> None:
    import argparse

    from eu_ai_check.e2e import cli

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(runner, "default_client",
                        lambda: pytest.fail("no client may be built without a key"))
    parser = argparse.ArgumentParser()
    cli.register(parser.add_subparsers(dest="cmd"))
    args = parser.parse_args(["assess", DESC])
    assert args.func(args) == 2
    err = capsys.readouterr().err
    assert "OPENAI_API_KEY is not set" in err and "docs/demo.md" in err


def test_answers_dated_after_the_2026_amendment_say_the_corpus_predates_it() -> None:
    base = {"answer": "## x", "fallback": False, "calls": [], "cost_usd": 0.0,
            "worst_case_usd": 0.6, "max_cost_usd": 0.75}
    after = A.render(base | {"relevant_date": "2026-11-01"})
    before = A.render(base | {"relevant_date": "2026-07-26"})
    assert A.AMENDMENT_NOTE in after and after.endswith(A.LIMITATIONS_POINTER)
    assert A.AMENDMENT_NOTE not in before
    assert A.AMENDMENT_NOTE not in A.render(base | {"relevant_date": None})

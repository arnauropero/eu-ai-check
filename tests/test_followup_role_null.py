"""Phase 2 follow-up `role-null-v1` -- offline tests (SYSTEM_SPEC.md §18).

These pin, before any follow-up API call: that the baseline prompt is still the
frozen one of the first run (8ef12af); the exact text of the approved
intervention and where it goes; that the frozen v1 results are untouched; and
that the pre-registered metrics and decision rule reproduce the baseline numbers
the follow-up was designed against. No test here makes a network call.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from eu_ai_check.extraction import followup
from eu_ai_check.extraction.extractor import (
    DEFAULT_PROMPT_VARIANT,
    FIELD_RULES,
    PROMPT_VARIANTS,
    ROLE_NULL_V1,
    SYSTEM_PROMPT,
    system_prompt,
)
from eu_ai_check.extraction.openai_extractor import OPENAI_MODEL, OpenAIExtractor

from test_extraction_openai import GOOD, TODAY, RoutingResponses, fake_openai, judge_response, openai_response

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "data/eval/extraction_cases.json"
FROZEN = ROOT / "data/eval/results/extraction_v1_30c.runs.jsonl"

#: SHA-256 of system_prompt() as sent in the first run (8ef12af).
FROZEN_SYSTEM_PROMPT = "80a13431265124b13da71f77a675788c8cf6a4a4d20934a990c1490582094428"

#: The intervention exactly as approved on 2026-10-02.
APPROVED_ROLE_NULL_V1 = (
    "CLARIFICATION: role_in_decision_making\n"
    "- Fill role_in_decision_making only when the description says what happens with the "
    "system's output: who acts on it or decides with it, or that the system itself acts or "
    "decides without a person. A description of the system's task alone (for example \"it "
    "translates text\" or \"it transcribes audio\") does not say how its output is used.\n"
    "- If the description does not say how the output is used, return null. Do not write a "
    "value that only says the role, the decision-maker or human involvement is not "
    "described."
)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# --------------------------------------------------------------------------
# Prompts
# --------------------------------------------------------------------------

def test_baseline_prompt_is_the_frozen_one_and_the_default() -> None:
    assert DEFAULT_PROMPT_VARIANT == "baseline"
    assert _sha(system_prompt()) == FROZEN_SYSTEM_PROMPT
    assert _sha(system_prompt("baseline")) == FROZEN_SYSTEM_PROMPT


def test_role_null_v1_is_the_approved_text_appended_after_the_field_rules() -> None:
    assert ROLE_NULL_V1 == APPROVED_ROLE_NULL_V1
    variant = system_prompt("role-null-v1")
    assert variant == system_prompt() + "\n\n" + APPROVED_ROLE_NULL_V1
    assert variant.index(FIELD_RULES) < variant.index("CLARIFICATION:")
    assert set(PROMPT_VARIANTS) == {"baseline", "role-null-v1"}


def test_intervention_examples_do_not_appear_in_any_eval_case() -> None:
    data = json.loads(CASES.read_text(encoding="utf-8"))
    text = " ".join(c["description"].lower() for c in data["cases"])
    assert "translat" not in text and "transcrib" not in text


def test_unknown_prompt_variant_is_rejected() -> None:
    with pytest.raises(ValueError):
        system_prompt("role-null-v2")
    with pytest.raises(ValueError):
        OpenAIExtractor(fake_openai(None), prompt_variant="role-null-v2")


@pytest.mark.parametrize("variant", ["baseline", "role-null-v1"])
def test_request_carries_the_variant_prompt_and_records_it(variant: str) -> None:
    client = fake_openai(openai_response(json.dumps(GOOD)))
    ex = OpenAIExtractor(client, effort="low", prompt_variant=variant)
    assert ex.request("x", TODAY)["input"][0]["content"] == system_prompt(variant)
    assert ex.extract("x", TODAY).prompt_variant == variant
    assert OpenAIExtractor(client).prompt_variant == "baseline"


def test_system_prompt_template_is_unchanged() -> None:
    """The variant is appended; the frozen template itself is not edited."""
    assert SYSTEM_PROMPT.endswith("FIELD RULES\n{rules}")


# --------------------------------------------------------------------------
# The four-arm run
# --------------------------------------------------------------------------

def test_cli_runs_four_arms_with_the_baseline_judge(monkeypatch, tmp_path) -> None:
    import openai

    from eu_ai_check import cli
    from eu_ai_check.extraction.evaluation import JUDGE_MODEL

    monkeypatch.setitem(sys.modules, "anthropic", None)
    routes = RoutingResponses(
        openai_response(json.dumps(GOOD)),
        judge_response([{"field": "intended_purpose", "same_fact": True, "reason": "ok"}]),
    )
    monkeypatch.setattr(openai, "OpenAI", lambda *a, **k: SimpleNamespace(responses=routes))
    out = tmp_path / "v2"
    cli.main(["eval-extraction", "--repeats", "1", "--prompt-variants", "baseline,role-null-v1",
              "--out", str(out)])
    records = [json.loads(x) for x in out.with_suffix(".runs.jsonl").read_text("utf-8").splitlines()]
    assert len(records) == 30 * 4
    assert {(r["prompt_variant"], r["effort"]) for r in records} == {
        (v, e) for v in ("baseline", "role-null-v1") for e in ("low", "medium")
    }
    assert set(routes.models) == {OPENAI_MODEL, JUDGE_MODEL}
    summary = json.loads(out.with_suffix(".json").read_text("utf-8"))
    assert set(summary) == {f"{OPENAI_MODEL} prompt={v} effort={e}"
                            for v in ("baseline", "role-null-v1") for e in ("low", "medium")}


# --------------------------------------------------------------------------
# Pre-registered metrics against the frozen baseline
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def frozen():
    return followup.load_records(FROZEN), followup.load_labels(CASES)


def test_frozen_v1_results_are_untouched(frozen) -> None:
    records, _ = frozen
    assert len(records) == 180
    assert {r["arm"] for r in records} == {"gpt-6.1-sol effort=low", "gpt-6.1-sol effort=medium"}
    assert all("prompt_variant" not in r for r in records)  # written before variants existed


def test_metrics_reproduce_the_baseline_the_followup_was_designed_against(frozen) -> None:
    records, labels = frozen
    low = followup.metrics(followup.arm(records, "low"), labels)
    med = followup.metrics(followup.arm(records, "medium"), labels)
    assert (low["false_fills"], low["false_fill_slots"], med["false_fills"]) == (5, 39, 9)
    assert (low["false_fills_clear"], med["false_fills_clear"]) == (0, 3)
    assert (low["absence_only_fills"], med["absence_only_fills"]) == (2, 4)
    assert (low["guard_misses"], low["guard_slots"], med["guard_misses"]) == (0, 51, 0)
    assert (low["leaks"], med["leaks"]) == (0, 0)


def _metric_set(frozen, effort):
    records, labels = frozen
    return followup.metrics(followup.arm(records, effort), labels)


def test_decision_rule_passes_only_when_every_check_holds(frozen) -> None:
    base = _metric_set(frozen, "medium")
    new = copy.deepcopy(base)
    new["false_fills"], new["false_fills_clear"] = 4, 0
    assert followup.decide(base, base, new)["passed"]

    for change in (
        {"false_fills": 9},                    # 1: total must go down
        {"false_fills_clear": 4},              # 1: E09+E14 must not go up
        {"guard_misses": 2},                   # 2: at most one new guard miss
        {"leaks": 1},                          # 4: zero leaks
    ):
        bad = copy.deepcopy(new)
        bad.update(change)
        assert not followup.decide(base, base, bad)["passed"], change


def test_decision_rule_other_fields_are_bounded_by_the_rerun_gap(frozen) -> None:
    base = _metric_set(frozen, "low")
    new = copy.deepcopy(base)
    new["false_fills"] = 0
    rerun = copy.deepcopy(base)
    f = "affected_persons"
    new["field_accuracy"][f] += 0.02            # moves, upward
    assert not followup.decide(base, rerun, new)["passed"]  # literal: either direction
    rerun["field_accuracy"][f] -= 0.02          # the re-run shows that much noise
    assert followup.decide(base, rerun, new)["passed"]
    new["field_accuracy"]["role_in_decision_making"] += 0.5  # role is not an "other field"
    assert followup.decide(base, rerun, new)["passed"]


def test_absence_pattern_matches_only_absence_statements() -> None:
    hits = [
        "The AI model is used to set individual premiums; any human involvement is not described.",
        "The model sets premiums; no human decision-making role is described.",
        "Banks integrate the SDK; no further decision process is described.",
    ]
    misses = [
        "Answers customers' questions directly through the webshop chatbot.",
        "Recruiters use the ranked shortlist and decide who to interview.",
    ]
    assert all(followup.ABSENCE_RE.search(h) for h in hits)
    assert not any(followup.ABSENCE_RE.search(m) for m in misses)


def test_posthoc_report_is_labelled_and_never_lowers_the_score(frozen) -> None:
    records, labels = frozen
    text = followup.posthoc_affected_persons(records, labels)
    assert text.startswith("POST-HOC ANALYSIS -- NOT PRE-REGISTERED")
    assert "frozen primary score   75/90" in text   # low, 0.833
    assert "frozen primary score   73/90" in text   # medium, 0.811

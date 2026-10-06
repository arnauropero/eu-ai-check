"""The Phase 4 harness with fake generators only (no client, no API, no spend).

Results and the ledger go to a temporary directory. The real results directory is
never written by the test suite.
"""

from __future__ import annotations

import json

import pytest

from eu_ai_check.generation import evaluation as ev
from eu_ai_check.generation.checks import is_explainable
from eu_ai_check.generation.generator import GenerationResult
from eu_ai_check.generation.inputs import Inputs
from eu_ai_check.generation.schema import Claim, GeneratedAnswer
from eu_ai_check.generation.spend import SpendTracker


class FakeGenerator:
    """Stands in for OpenAIGenerator: same attributes, no client."""

    def __init__(self, mode: str = "explain", fail_refs: set[str] = frozenset()):
        self.model, self.effort, self.max_output_tokens = "fake-model", "low", 5000
        self.mode, self.fail_refs, self.calls = mode, set(fail_refs), 0

    def generate(self, inp) -> GenerationResult:
        self.calls += 1
        if inp.case_ref in self.fail_refs:
            raise ConnectionError("simulated APIConnectionError")
        claims = []
        if self.mode == "explain":
            for d in inp.determinations:
                if is_explainable(d) and d.supporting_evidence:
                    e = d.supporting_evidence[-1]
                    claims.append(Claim(claim_id=f"c{len(claims) + 1}",
                                        determination_id=d.determination_id,
                                        text=f"{e.provision_citation} is the provision behind "
                                             "this line.", evidence_ids=[e.evidence_id],
                                        quote=None, quote_evidence_id=None))
        if self.mode == "contradict":
            d = inp.determinations[0]
            claims.append(Claim(claim_id="c1", determination_id=d.determination_id,
                                text="This could not be determined and is not applicable.",
                                evidence_ids=[d.supporting_evidence[0].evidence_id]
                                if d.supporting_evidence else [], quote=None,
                                quote_evidence_id=None))
        if self.mode == "truncate":
            return GenerationResult(None, None, "incomplete response (max_output_tokens)",
                                    "fake-model", 4000, 5000, 4900, "incomplete:max_output_tokens")
        return GenerationResult(GeneratedAnswer(claims=claims), "{}", None, "fake-model",
                                4000, 2000, 1500, "completed")


@pytest.fixture(scope="module")
def inputs() -> Inputs:
    return Inputs()


def tracker(tmp_path, inputs, cap=5.0) -> SpendTracker:
    t = ev.make_tracker(inputs, 5000, ledger=tmp_path / "spend.jsonl")
    t.cap = cap
    return t


def test_smoke_item_is_the_largest_dev_record(inputs) -> None:
    item = ev.smoke_item(inputs)
    assert item.ref == "D41#1" and not item.synthetic
    assert item.ref.split("#")[0] in inputs.split["dev"]["cases"]
    assert len(item.input.determinations) == 6


def test_smoke_run_with_fake_generator(tmp_path, inputs) -> None:
    t = tracker(tmp_path, inputs)
    r = ev.run_smoke(FakeGenerator(), t, inputs)
    assert r.raw_passed and r.shipped_passed and not r.fallback
    assert t.calls("smoke") == 1


def test_dev_run_writes_results_and_scores(tmp_path, inputs) -> None:
    t = tracker(tmp_path, inputs)
    s = ev.run_dev(FakeGenerator(), t, inputs, "test", out_dir=tmp_path)
    assert s["records"] == 31                       # 28 dev cases at repeat 0 + S01-S03
    assert s["raw_pass_rate"] == 1.0 and s["fallback_rate"] == 0.0
    assert s["grounded_explanation_coverage"] == 1.0
    assert s["shipped_pass_rate"] == 1.0
    assert (tmp_path / "generation_dev_test.runs.jsonl").exists()
    assert t.calls("dev") == 31
    from eu_ai_check.generation.generator import PROMPT_HISTORY, PROMPT_VERSION, prompt_sha
    for e in t.entries:                              # every call is versioned in the ledger
        assert (e["prompt_version"], e["prompt_sha"], e["prompt_note"]) == (
            PROMPT_VERSION, prompt_sha(), PROMPT_HISTORY[PROMPT_VERSION])
    assert s["prompt_version"] == PROMPT_VERSION


def test_contradicting_claims_fall_back_to_the_template(tmp_path, inputs) -> None:
    s = ev.run_dev(FakeGenerator("contradict"), tracker(tmp_path, inputs), inputs, "bad",
                   refs=["D01#0", "D10#0"], out_dir=tmp_path)
    assert s["raw_pass_rate"] == 0.0 and s["fallback_rate"] == 1.0
    assert s["c4_violations"] == 2
    assert s["shipped_pass_rate"] == 1.0            # the template is what ships


def test_dev_stops_hard_before_eating_the_reserve(tmp_path, inputs) -> None:
    t = tracker(tmp_path, inputs, cap=2.6)          # reserve ~$2.4 leaves room for ~3 calls
    gen = FakeGenerator()
    s = ev.run_dev(gen, t, inputs, "budget", out_dir=tmp_path)
    assert s["stopped_by_budget"] and s["records"] < 31
    assert t.spent + t.reserve() <= 2.6


def test_heldout_needs_confirmation(tmp_path, inputs) -> None:
    with pytest.raises(ev.HeldoutAlreadyRun):
        ev.run_heldout(FakeGenerator(), tracker(tmp_path, inputs), inputs, confirm=False,
                       results_dir=tmp_path)


def test_heldout_runs_exactly_once_and_counts_api_failures(tmp_path, inputs) -> None:
    t = tracker(tmp_path, inputs)
    gen = FakeGenerator(fail_refs={"D02#0", "D38#1"})
    out = ev.run_heldout(gen, t, inputs, confirm=True, results_dir=tmp_path)
    assert gen.calls == 39 and out["records"] == 39
    assert out["api_errors"] == 2
    assert out["raw_pass_rate"] == round(37 / 39, 4)
    assert out["fallback_rate"] == round(2 / 39, 4)
    assert out["shipped_pass_rate"] == 1.0
    assert out["gold_differs_records"] == 15 and out["faithful_reproduction_rate"] == 1.0
    assert out["decision"]["hygiene"]["raw_pass_rate>=0.95"] is False
    assert out["decision"]["phase4_generator"] == "template"
    assert (tmp_path / "generation_heldout.started").exists()
    with pytest.raises(ev.HeldoutAlreadyRun):
        ev.run_heldout(gen, t, inputs, confirm=True, results_dir=tmp_path)
    assert gen.calls == 39


def test_heldout_marker_alone_blocks_a_rerun(tmp_path, inputs) -> None:
    (tmp_path / "generation_heldout.started").write_text("{}", encoding="utf-8")
    with pytest.raises(ev.HeldoutAlreadyRun):
        ev.run_heldout(FakeGenerator(), tracker(tmp_path, inputs), inputs, confirm=True,
                       results_dir=tmp_path)


def test_counts_only_never_contains_answer_text(tmp_path, inputs) -> None:
    out = ev.run_heldout(FakeGenerator(), tracker(tmp_path, inputs), inputs, confirm=True,
                         results_dir=tmp_path)
    printed = ev.counts_only(out)
    assert "Sources:" not in printed and "##" not in printed
    assert json.loads(printed)["records"] == 39


def test_decision_rule() -> None:
    good = {"raw_pass_rate": 0.97, "fallback_rate": 0.03, "c4_violations": 0,
            "c8_violations": 0, "grounded_explanation_coverage": 0.92,
            "shipped_pass_rate": 1.0, "faithful_reproduction_rate": 1.0}
    assert ev.decide(good)["phase4_generator"] == "pending pairwise rating"
    assert ev.decide(good, pairwise_llm_wins=15)["phase4_generator"] == "llm"
    assert ev.decide(good, pairwise_llm_wins=14)["phase4_generator"] == "template"
    assert ev.decide({**good, "c8_violations": 1}, 20)["phase4_generator"] == "template"
    assert ev.decide({**good, "grounded_explanation_coverage": 0.89}, 20)["adopt_llm"] is False
    assert ev.decide({**good, "fallback_rate": 0.051}, 20)["adopt_llm"] is False
    assert ev.decide(good, 20)["phase4_acceptance_met"] is True


def test_truncation_is_reported_apart_from_bad_content(tmp_path, inputs) -> None:
    t = tracker(tmp_path, inputs)
    trunc = ev.run_dev(FakeGenerator("truncate"), t, inputs, "trunc", refs=["D01#0"],
                       out_dir=tmp_path)
    assert trunc["truncated"] == 1 and trunc["content_failures"] == 0
    assert trunc["finish_reasons"] == {"incomplete:max_output_tokens": 1}
    bad = ev.run_dev(FakeGenerator("contradict"), t, inputs, "bad2", refs=["D01#0"],
                     out_dir=tmp_path)
    assert bad["truncated"] == 0 and bad["content_failures"] == 1
    entries = [json.loads(l) for l in (tmp_path / "spend.jsonl").read_text().splitlines()]
    assert entries[0]["finish_reason"] == "incomplete:max_output_tokens"
    assert entries[0]["reasoning_tokens"] == 4900


def test_c4_failures_record_phrase_and_claim(tmp_path, inputs) -> None:
    s = ev.run_dev(FakeGenerator("contradict"), tracker(tmp_path, inputs), inputs, "diag",
                   refs=["D10#0"], out_dir=tmp_path)
    row = json.loads((tmp_path / "generation_dev_diag.runs.jsonl").read_text().splitlines()[0])
    diags = row["c4_diagnostics"]
    assert {"check": "C4", "lexicon": "NOT_APPLICABLE_LEX", "phrase": "not applicable"}.items()         <= next(d for d in diags if d["phrase"] == "not applicable").items()
    assert all(d["claim_text"] == "This could not be determined and is not applicable."
               for d in diags if d["kind"] == "claim_lexicon")
    assert s["c4_c8_diagnostic_hits"] == {"C4:NOT_APPLICABLE_LEX": 1}


def test_amended_reserve_matches_the_tracker(inputs) -> None:
    """The 2026-10-06 amendment's numbers are what the tracker computes."""
    from pathlib import Path

    from eu_ai_check.generation.generator import MAX_OUTPUT_TOKENS
    text = Path("SYSTEM_SPEC.md").read_text(encoding="utf-8")
    assert "`max_output_tokens` = 5,000 per call" in text and MAX_OUTPUT_TOKENS == 5000
    assert "**That gives $2.3875.**" in text
    t = ev.make_tracker(inputs, MAX_OUTPUT_TOKENS, ledger=Path("does-not-exist.jsonl"))
    # The 2026-10-06 note (plain-word explanations) moved it by $0.0010; the rule
    # is unchanged and can only make the reserve stricter.
    assert "$2.3875 to $2.3885" in text
    # Prompt v2 (longer system prompt) moved it again, same rule (§18 note).
    assert "$2.3963 under the unchanged reserve rule" in text
    assert round(t.heldout_worst_case, 4) == 2.3963
    assert round(t.dev_budget_left(), 2) == 2.60

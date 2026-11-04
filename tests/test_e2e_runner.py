"""The Phase 5 runner with a fake OpenAI client only (no key, no network, no spend).

The fake answers from the labels (tests/e2e_fakes.py), so these tests check the
plumbing: stage chain, pinned clock, guards, spend, retry policy, gates and
attribution. A fake run is a plumbing check, never a result. Results and ledgers go
to temporary directories; the real results directory is never written.
"""

from __future__ import annotations

import ast
import json
import sys
from datetime import date
from pathlib import Path

import httpx
import openai
import pytest

from e2e_fakes import FakeOpenAI, oracle_verdicts
from eu_ai_check.e2e import runner, scoring
from eu_ai_check.e2e.spend import E2ESpend

CASES = [c for _, c in runner.heldout_cases()]
BY_ID = {c["id"]: c for c in CASES}
SPEC = Path("SYSTEM_SPEC.md").read_text(encoding="utf-8")


def stages(fake: FakeOpenAI) -> runner.Stages:
    return runner.Stages.frozen(runner.RecordingClient(fake))


TREE = {"paths": {"src/eu_ai_check": "t1"}, "combined_sha256": "c1"}


def tree():
    return TREE


def heldout(tmp_path, fake: FakeOpenAI, **kw) -> tuple[dict, E2ESpend]:
    t = E2ESpend(tmp_path / "spend.jsonl", eval_clock=runner.EVAL_CLOCK)
    return runner.run_heldout(stages(fake), t, confirm=True, results_dir=tmp_path, tree=tree,
                              **kw), t


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(runner.time, "sleep", lambda s: None)
    monkeypatch.setattr(runner.gen_eval.time, "sleep", lambda s: None)


@pytest.fixture(scope="module")
def oracle_run(tmp_path_factory):
    d = tmp_path_factory.mktemp("heldout")
    return heldout(d, FakeOpenAI(CASES)) + (d,)


# --------------------------------------------------------------------------
# Pinned clock
# --------------------------------------------------------------------------

def test_clock_is_pinned() -> None:
    assert runner.EVAL_CLOCK == date(2026, 10, 25)
    assert runner.CORPUS_SNAPSHOT == date(2024, 8, 1)
    dev = {c["id"]: c for c in runner.load(runner.DEV_CASES)}
    for c in CASES + [dev[i] for i in runner.STABILITY_CASES]:
        assert runner.assessment_date(c) <= runner.EVAL_CLOCK
    assert runner.assessment_date({"id": "X", "assessment_date": None}) == runner.EVAL_CLOCK
    with pytest.raises(ValueError, match="after the pinned clock"):
        runner.assessment_date({"id": "X", "assessment_date": "2026-10-26"})


CLOCK_CALLS = {"today", "now", "utcnow", "time", "time_ns", "monotonic", "perf_counter",
               "localtime", "gmtime", "ctime"}
#: The Phase 4 tracker's ledger timestamp. Imported with generation.evaluation, but
#: never called on the Phase 5 path (asserted at runtime below).
ALLOWED = {("eu_ai_check.generation.spend", "datetime.now")}


def test_no_system_clock_call_in_the_evaluation_path() -> None:
    """Every module the runner loads, read statically."""
    found = []
    for name in sorted(m for m in sys.modules if m.startswith("eu_ai_check")):
        path = getattr(sys.modules[name], "__file__", None)
        if not path or name.endswith(".cli") or name == "eu_ai_check.cli":
            continue
        tree = ast.parse(Path(path).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in CLOCK_CALLS
                    and (name, ast.unparse(node.func)) not in ALLOWED):
                found.append(f"{name}:{node.lineno} {ast.unparse(node.func)}")
    assert "eu_ai_check.e2e.runner" in sys.modules
    assert not found


def test_full_path_never_reaches_the_phase_4_clocked_tracker(tmp_path, monkeypatch) -> None:
    from eu_ai_check.generation import spend as phase4

    def forbidden(*a, **k):
        raise AssertionError("Phase 4 tracker (wall-clock ledger) used on the Phase 5 path")

    monkeypatch.setattr(phase4.SpendTracker, "record", forbidden)
    monkeypatch.setattr(phase4.SpendTracker, "guard", forbidden)
    out, _ = heldout(tmp_path, FakeOpenAI(CASES))
    assert out["eval_clock"] == "2026-10-25"


# --------------------------------------------------------------------------
# The stage chain, the ledger and the guards
# --------------------------------------------------------------------------

def test_plumbing_check_labels_survive_the_real_pipeline(oracle_run) -> None:
    """With verdicts that encode the labels, the shipped code reproduces every label:
    a plumbing and label-consistency check, not a result."""
    out, _, _ = oracle_run
    g = out["gates"]
    assert (g["G3a_risk_tier_prohibited"]["k"], g["G3a_risk_tier_prohibited"]["n"]) == (24, 24)
    assert (g["G3b_roles_obligations"]["k"], g["G3b_roles_obligations"]["n"]) == (36, 36)
    assert (g["G4_citations"]["k"], g["G4_citations"]["n"]) == (10, 10)
    assert g["G1_zero_tolerance"]["root_causes"] == 0
    assert g["G2_output_hygiene"]["k"] == 20
    assert out["phase5_acceptance_met"] is True
    assert out["stratum_B"]["cases"] == 8 and out["stratum_A"]["cases"] == 12


def test_ledger_has_every_stage_and_n05_never_calls_the_matcher(oracle_run) -> None:
    _, t, _ = oracle_run
    stages_ = [(e["stage"], e["ref"]) for e in t.entries]
    assert len(stages_) == 59
    assert sum(s == "extract" for s, _ in stages_) == 20
    assert sum(s == "match" for s, _ in stages_) == 19
    assert sum(s == "generate" for s, _ in stages_) == 20
    assert ("match", "N05#0") not in stages_
    assert all(e["phase"] == "heldout" and "ts" not in e for e in t.entries)
    assert all(e["response_id"] for e in t.entries)


def test_results_and_marker_are_written(oracle_run) -> None:
    out, _, d = oracle_run
    marker = json.loads((d / "e2e_heldout.started").read_text(encoding="utf-8"))
    assert marker["eval_clock"] == "2026-10-25"
    assert marker["e2e_cases_sha256"] == out["e2e_cases_sha256"]
    assert marker["generator"]["prompt_sha"] == runner.GENERATION_PROMPT_SHA
    assert marker["scored_tree"] == TREE
    rows = (d / "e2e_heldout.runs.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(r)["case_id"] for r in rows] == [c["id"] for c in CASES]


def test_heldout_runs_exactly_once(tmp_path) -> None:
    fake = FakeOpenAI(CASES)
    t = E2ESpend(tmp_path / "s.jsonl", eval_clock=runner.EVAL_CLOCK)
    with pytest.raises(runner.HeldoutAlreadyRun, match="confirmation"):
        runner.run_heldout(stages(fake), t, confirm=False, results_dir=tmp_path, tree=tree)
    runner.run_heldout(stages(fake), t, confirm=True, results_dir=tmp_path, tree=tree)
    calls = len(fake.calls)
    with pytest.raises(runner.HeldoutAlreadyRun):
        runner.run_heldout(stages(fake), t, confirm=True, results_dir=tmp_path, tree=tree)
    assert len(fake.calls) == calls


def test_a_crashed_heldout_pass_cannot_be_rerun(tmp_path) -> None:
    (tmp_path / "e2e_heldout.started").write_text("{}", encoding="utf-8")
    fake = FakeOpenAI(CASES)
    t = E2ESpend(tmp_path / "s.jsonl", eval_clock=runner.EVAL_CLOCK)
    with pytest.raises(runner.HeldoutAlreadyRun):
        runner.run_heldout(stages(fake), t, confirm=True, results_dir=tmp_path, tree=tree)
    assert fake.calls == []


def test_only_the_frozen_configuration_runs(tmp_path) -> None:
    fake = FakeOpenAI(CASES)
    s = stages(fake)
    s.matcher.effort = "medium"
    t = E2ESpend(tmp_path / "s.jsonl", eval_clock=runner.EVAL_CLOCK)
    with pytest.raises(RuntimeError, match="frozen configuration"):
        runner.run_heldout(s, t, confirm=True, results_dir=tmp_path, tree=tree)
    assert not (tmp_path / "e2e_heldout.started").exists() and fake.calls == []


def test_frozen_configuration_values() -> None:
    s = stages(FakeOpenAI(CASES))
    s.check_frozen()
    assert (s.extractor.model, s.extractor.effort, s.extractor.prompt_variant) == (
        "gpt-6.1-sol", "low", "baseline")
    assert (s.matcher.model, s.matcher.effort, s.matcher.prompt_variant) == (
        "gpt-6.1-sol", "low", "baseline")
    assert (s.generator.model, s.generator.effort, s.generator.max_output_tokens) == (
        "gpt-6.1-sol", "low", 5000)


# --------------------------------------------------------------------------
# Resume rule (§18 addition, 2026-11-04)
# --------------------------------------------------------------------------

def _interrupt_at(case_id):
    def boom(stage, case, n):
        if stage == "provision_verdicts" and case["id"] == case_id:
            return KeyboardInterrupt()
        return None
    return boom


def _recorded(tmp_path) -> list[str]:
    return [json.loads(l)["case_id"] for l in
            (tmp_path / "e2e_heldout.runs.jsonl").read_text(encoding="utf-8").splitlines()]


def test_resume_continues_only_cases_without_a_recorded_result(tmp_path, oracle_run) -> None:
    with pytest.raises(KeyboardInterrupt):
        heldout(tmp_path, FakeOpenAI(CASES, raise_on=_interrupt_at("N07")))
    recorded = _recorded(tmp_path)
    assert recorded == ["N01", "N02", "N03", "N04", "N05", "N06"]
    with pytest.raises(runner.HeldoutAlreadyRun):          # a fresh start is refused
        heldout(tmp_path, FakeOpenAI(CASES))
    again = FakeOpenAI(CASES)
    out, _ = heldout(tmp_path, again, resume=True)
    called = {ref for _, ref in again.calls if ref != "gen"}
    assert called == {c["id"] for c in CASES} - set(recorded)
    assert out["resumed"] is True
    full, _, _ = oracle_run
    assert out["gates"] == full["gates"]                  # restored cases score the same
    assert sorted(_recorded(tmp_path)) == sorted(c["id"] for c in CASES)
    with pytest.raises(runner.ResumeRefused, match="complete"):
        heldout(tmp_path, FakeOpenAI(CASES), resume=True)


def test_resume_is_refused_if_the_scored_tree_changed(tmp_path, monkeypatch) -> None:
    with pytest.raises(KeyboardInterrupt):
        heldout(tmp_path, FakeOpenAI(CASES, raise_on=_interrupt_at("N03")))
    monkeypatch.setitem(TREE, "combined_sha256", "c2")
    fake = FakeOpenAI(CASES)
    with pytest.raises(runner.ResumeRefused, match="tree hash"):
        heldout(tmp_path, fake, resume=True)
    assert fake.calls == []


def test_resume_without_a_started_pass_is_refused(tmp_path) -> None:
    with pytest.raises(runner.ResumeRefused, match="no held-out marker"):
        heldout(tmp_path, FakeOpenAI(CASES), resume=True)


def test_resume_refuses_a_recorded_result_that_does_not_replay(tmp_path) -> None:
    with pytest.raises(KeyboardInterrupt):
        heldout(tmp_path, FakeOpenAI(CASES, raise_on=_interrupt_at("N03")))
    runs = tmp_path / "e2e_heldout.runs.jsonl"
    rows = [json.loads(l) for l in runs.read_text(encoding="utf-8").splitlines()]
    rows[0]["predicted"]["risk_tier"] = "unresolved"
    runs.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    fake = FakeOpenAI(CASES)
    with pytest.raises(runner.ResumeRefused, match="does not replay"):
        heldout(tmp_path, fake, resume=True)
    assert fake.calls == []


def _git(root, *args) -> None:
    import subprocess
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c",
                    "core.autocrlf=false", *args], cwd=root, check=True, capture_output=True)


def test_scored_tree_hash_requires_a_clean_scored_path(tmp_path) -> None:
    for p in runner.SCORED_PATHS:
        f = tmp_path / p if Path(p).suffix else tmp_path / p / "x.py"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("1\n", encoding="utf-8")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "x")
    first = runner.scored_tree(tmp_path)
    assert set(first["paths"]) == set(runner.SCORED_PATHS)
    assert runner.scored_tree(tmp_path) == first
    (tmp_path / "src/eu_ai_check/x.py").write_text("2\n", encoding="utf-8")
    with pytest.raises(runner.ScoredTreeError):
        runner.scored_tree(tmp_path)
    _git(tmp_path, "commit", "-qam", "y")
    assert runner.scored_tree(tmp_path)["combined_sha256"] != first["combined_sha256"]
    (tmp_path / "data/corpus/new.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(runner.ScoredTreeError):              # untracked counts too
        runner.scored_tree(tmp_path)
    (tmp_path / "data/corpus/new.json").unlink()
    (tmp_path / "notes.txt").write_text("not scored\n", encoding="utf-8")
    runner.scored_tree(tmp_path)                             # outside the scored paths


def test_scored_paths_cover_code_corpus_cases_and_locks() -> None:
    assert runner.SCORED_PATHS == ("src/eu_ai_check", "data/corpus", "data/eval/e2e_cases.json",
                                   "data/eval/determination_heldout_5_1_c.json",
                                   "pyproject.toml", "uv.lock")
    assert all(Path(p).exists() for p in runner.SCORED_PATHS)


# --------------------------------------------------------------------------
# API-failure policy
# --------------------------------------------------------------------------

REQ = httpx.Request("POST", "https://api.openai.com/v1/responses")


def test_transient_error_is_retried_guarded_and_billed(tmp_path) -> None:
    def flaky(stage, case, n):
        if stage == "case_facts" and case["id"] == "N01" and n == 0:
            return openai.APIConnectionError(request=REQ)
        return None

    out, t = heldout(tmp_path, FakeOpenAI(CASES, raise_on=flaky))
    n01 = [e for e in t.entries if e["ref"] == "N01#0" and e["stage"] == "extract"]
    assert [(e["attempt"], e["error"] is not None) for e in n01] == [(1, True), (2, False)]
    assert n01[0]["transient"] is True
    assert "N01" not in out["stage_failures"]


def test_429_fails_at_once_ships_nothing_and_is_scored_wrong(tmp_path) -> None:
    def limited(stage, case, n):
        if stage == "provision_verdicts" and case["id"] == "N02":
            return openai.RateLimitError("rate limited", response=httpx.Response(429, request=REQ),
                                         body=None)
        return None

    fake = FakeOpenAI(CASES, raise_on=limited)
    out, t = heldout(tmp_path, fake)
    assert fake.calls.count(("provision_verdicts", "N02")) == 1          # not retried
    assert out["stage_failures"]["N02"].startswith("match:")
    assert "N02" in out["gates"]["G2_output_hygiene"]["failures"]
    assert out["gates"]["G2_output_hygiene"]["pass"] is False
    n02 = [m for m in out["misses"] if m["case"] == "N02"]
    assert len(n02) == 5 and {m["stage"] for m in n02} == {"stage_failure:match"}
    assert out["phase5_acceptance_met"] is False


# --------------------------------------------------------------------------
# Gates and attribution
# --------------------------------------------------------------------------

def test_g1_is_counted_by_root_cause(tmp_path) -> None:
    """A D32-type event on H08: definite verdicts where the label is unresolved."""
    def flip(case, cands):
        if case["id"] != "H08":
            return oracle_verdicts(case, cands)
        return [{"provision_id": p, "rationale": "x", "verdict": "does_not_match",
                 "case_quote": case["description"][:20], "provision_quote": None}
                for p in cands]

    out, _ = heldout(tmp_path, FakeOpenAI(CASES, verdicts=flip))
    g1 = out["gates"]["G1_zero_tolerance"]
    assert g1["pass"] is False and out["phase5_acceptance_met"] is False
    assert set(g1["violations"]) == {"H08: matcher verdicts (risk_tier)",
                                     "H08: matcher verdicts (prohibited_practice)"}
    assert g1["root_causes"] == 2
    cascade = [v["item"] for v in g1["violations"]["H08: matcher verdicts (risk_tier)"]]
    assert cascade[0].startswith("risk_tier: unresolved ->") and len(cascade) >= 2
    # Stratum B never enters G3.
    assert out["gates"]["G3a_risk_tier_prohibited"]["k"] == 24


def test_extraction_leak_is_a_g1_violation(tmp_path) -> None:
    def leaky(case):
        f = dict(case["gold_facts"])
        if case["id"] == "N09":
            f["intended_purpose"] += " (a high-risk system)"
        return f

    out, _ = heldout(tmp_path, FakeOpenAI(CASES, facts=leaky))
    assert "N09: extraction leakage" in out["gates"]["G1_zero_tolerance"]["violations"]


def test_actor_fact_miss_is_attributed_to_extraction(tmp_path) -> None:
    def wrong(case):
        f = dict(case["gold_facts"])
        if case["id"] == "N01":
            f["puts_own_name_or_trademark"] = False
        return f

    out, _ = heldout(tmp_path, FakeOpenAI(CASES, facts=wrong))
    n01 = [m for m in out["misses"] if m["case"] == "N01"]
    assert n01 and {m["stage"] for m in n01} == {"extraction"}
    assert {m["key"] for m in n01} <= set(scoring.RULE_KEYS)


def test_date_miss_is_attributed_to_date_extraction(tmp_path) -> None:
    def undated(case):
        f = dict(case["gold_facts"])
        if case["id"] == "N03":
            f["relevant_date"] = None        # falls back to 2026-10-25
        return f

    out, _ = heldout(tmp_path, FakeOpenAI(CASES, facts=undated))
    n03 = [m for m in out["misses"] if m["case"] == "N03"]
    assert any(m["key"] == "risk_tier" for m in n03)
    assert {m["stage"] for m in n03} == {"date_extraction"}


def test_wilson_intervals_of_the_thresholds() -> None:
    assert scoring.wilson(19, 24) == (0.595, 0.908)
    assert scoring.wilson(33, 36) == (0.782, 0.971)
    assert scoring.wilson(10, 10) == (0.722, 1.0)
    assert scoring.wilson(0, 0) is None


def test_thresholds_match_the_preregistration() -> None:
    assert (scoring.G3A_MIN, scoring.G3A_N) == (19, 24)
    assert (scoring.G3B_MIN, scoring.G3B_N) == (33, 36)
    assert scoring.G4_MAX_MISMATCHES == 0
    for text in ("**G3a** (risk tier + prohibited practice, n = 24): pass if **≥ 19/24**",
                 "**G3b** (actor roles + Article 16 + Article 26, n = 36): pass if **≥ 33/36**",
                 "**G4** (citation grounds, n ≤ 10): pass if **0 mismatches**"):
        assert text in SPEC


# --------------------------------------------------------------------------
# Stability
# --------------------------------------------------------------------------

def _dev():
    dev = {c["id"]: c for c in runner.load(runner.DEV_CASES)}
    return [dev[i] for i in runner.STABILITY_CASES]


def test_stability_runs_in_rounds(tmp_path) -> None:
    fake = FakeOpenAI(_dev())
    t = E2ESpend(tmp_path / "s.jsonl", eval_clock=runner.EVAL_CLOCK)
    out = runner.run_stability(stages(fake), t, results_dir=tmp_path)
    order = [r for s, r in fake.calls if s == "case_facts"]
    assert order == list(runner.STABILITY_CASES) * 3
    assert out["complete_rounds"] == 3 and out["partial_round"] is None
    assert all(e["phase"] == "stability" for e in t.entries)
    with pytest.raises(runner.StabilityAlreadyRun):
        runner.run_stability(stages(fake), t, results_dir=tmp_path)


def test_stability_stops_at_its_cap(tmp_path) -> None:
    fake = FakeOpenAI(_dev(), tokens={"provision_verdicts": (8000, 30_000)})   # $0.316 each
    t = E2ESpend(tmp_path / "s.jsonl", eval_clock=runner.EVAL_CLOCK)
    out = runner.run_stability(stages(fake), t, results_dir=tmp_path)
    assert out["stopped_by_budget"] and "stability cap" in out["stopped_by_budget"]
    assert t.spent_in("stability") <= 1.50
    assert out["complete_rounds"] < 3


def test_stability_statistics_use_complete_rounds_only() -> None:
    from eu_ai_check.e2e.runner import CaseRun

    def run(cid, rep, tier):
        return CaseRun(cid, "dev", rep, "2026-10-03", predicted={
            "risk_tier": tier, "prohibited_practice": "x", "actor_role": {}, "aiact:art_16": "x",
            "aiact:art_26": "x"})

    cases = {c["id"]: c for c in _dev()}
    runs = [run(c, 0, "a") for c in cases] + [run(c, 1, "a") for c in cases] + [
        run("D32", 2, "CHANGED")]
    out = scoring.score_stability(runs, cases, runner._Env().pipeline)
    assert out["complete_rounds"] == 2
    assert out["partial_round"]["runs_excluded_from_statistics"] == ["D32#2"]
    assert out["per_case"]["D32"]["repeats"] == 2
    assert out["per_case"]["D32"]["stable"]["risk_tier"] is True


def test_stability_stops_after_a_failed_smoke_run(tmp_path) -> None:
    def broken(stage, case, n):
        return openai.BadRequestError("bad", response=httpx.Response(400, request=REQ), body=None)

    fake = FakeOpenAI(_dev(), raise_on=broken)
    t = E2ESpend(tmp_path / "s.jsonl", eval_clock=runner.EVAL_CLOCK)
    out = runner.run_stability(stages(fake), t, results_dir=tmp_path)
    assert out["smoke_test_failed"].startswith("extract:")
    assert len(fake.calls) == 1 and out["runs"] == 1


def test_additions_are_in_the_preregistration() -> None:
    assert "**Wiring-fix carve-out.**" in SPEC
    assert "**Resume rule for the held-out pass.**" in SPEC
    for p in ("`src/eu_ai_check`", "`data/corpus`", "`pyproject.toml`", "`uv.lock`"):
        assert p in SPEC

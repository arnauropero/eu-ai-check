"""The Phase 5 demo recording, with a fake OpenAI client only (no key, no network).
A fake recording is a plumbing check, never a demo. Output goes to temporary
directories; data/demo/ is never written by the test suite."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

import test_determination_eval as phase3
from e2e_fakes import FakeOpenAI
from eu_ai_check.e2e import demo, runner
from eu_ai_check.e2e.spend import E2ESpend
from test_e2e_cases import RELATIVE_TIME

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = demo.load_examples(ROOT / "data/demo/examples.json")

#: Valid extraction output for the fake: X03 is dated 2024-06-01, the rest 2026-11-01.
FACTS = {"intended_purpose": "demo", "ai_functionality": "demo", "relevant_date": None,
         "role_in_decision_making": None, "develops_system": True, "uses_system": True,
         "puts_own_name_or_trademark": True, "modifies_intended_purpose": None,
         "modifies_safety_relevant_components": None, "jurisdiction": None,
         "affected_persons": None}


def fake_cases() -> list[dict]:
    out = []
    for x in EXAMPLES:
        on = "2024-06-01" if x["id"] == "X03" else "2026-11-01"
        out.append({**x, "gold_facts": {**FACTS, "relevant_date": on}})
    return out


def insufficient(case, cands):
    return [{"provision_id": p, "rationale": "fake", "verdict": "insufficient",
             "case_quote": None, "provision_quote": None} for p in cands]


def record(tmp_path, fake=None, heldout_done=True):
    fake = fake or FakeOpenAI(fake_cases(), verdicts=insufficient)
    summary = tmp_path / "e2e_heldout.json"
    if heldout_done:
        summary.write_text("{}", encoding="utf-8")
    t = E2ESpend(tmp_path / "spend.jsonl", eval_clock=runner.EVAL_CLOCK)
    t.record("heldout", "extract", "N01#0", "m", 1000, 500)          # held-out started
    out = demo.run_demo(runner.Stages.frozen(runner.RecordingClient(fake)), t,
                        out_dir=tmp_path / "demo", heldout_summary=summary)
    return out, t, fake


# --------------------------------------------------------------------------
# The examples
# --------------------------------------------------------------------------

def test_five_examples_cover_the_approved_situations() -> None:
    assert [x["id"] for x in EXAMPLES] == ["X01", "X02", "X03", "X04", "X05"]
    assert [x["covers"] for x in EXAMPLES] == [
        "applicable", "unresolved, with follow-up questions", "not_in_force",
        "a prohibited practice", "Document completeness check"]
    assert [x["id"] for x in EXAMPLES if x["note"]] == ["X04", "X05"]
    assert "prohibited practice applies" in EXAMPLES[3]["note"]
    x05 = EXAMPLES[4]
    assert x05["chosen_to_cover"] == "a cautious unresolved"
    assert x05["annotation_revised"] == "2026-11-04"
    for phrase in ("chosen to show caution", "answers the profiling question",
                   "Article 6(3)(b)", "no previous human activity",
                   "not that applying it is correct"):
        assert phrase in x05["note"], phrase


def test_example_dates_are_explicit_and_not_after_the_demo_clock() -> None:
    for x in EXAMPLES:
        want = "1 June 2024" if x["id"] == "X03" else "1 November 2026"
        assert want in x["description"], x["id"]
        text = x["description"].lower()
        assert not [w for w in RELATIVE_TIME if f" {w}" in f" {text}"], x["id"]
        assert runner.assessment_date(x, demo.DEMO_CLOCK) == demo.DEMO_CLOCK
    assert date(2026, 11, 1) <= demo.DEMO_CLOCK


def test_examples_are_not_taken_from_evaluation_or_dev_data() -> None:
    seen = {c["description"] for c in phase3.CASES}
    for p in ("data/eval/extraction_cases.json", "data/eval/e2e_cases.json",
              "data/eval/determination_heldout_5_1_c.json"):
        seen |= {c["description"] for c in
                 json.loads((ROOT / p).read_text(encoding="utf-8"))["cases"]}
    assert not seen & {x["description"] for x in EXAMPLES}


def test_demo_clock_is_its_own_and_the_scored_clock_is_untouched() -> None:
    assert demo.DEMO_CLOCK == date(2026, 11, 4)
    assert runner.EVAL_CLOCK == date(2026, 10, 25)
    with pytest.raises(ValueError, match="after the pinned clock"):
        runner.assessment_date(EXAMPLES[0])             # the scored path still refuses it


# --------------------------------------------------------------------------
# Recording
# --------------------------------------------------------------------------

def test_recording_saves_inputs_unedited_outputs_models_dates_and_spend(tmp_path) -> None:
    out, t, _ = record(tmp_path)
    d = tmp_path / "demo"
    saved = json.loads((d / "recording.json").read_text(encoding="utf-8"))
    assert saved["demo_clock"] == "2026-11-04" and saved["model"] == "gpt-6.1-sol"
    for x, ex in zip(saved["examples"], EXAMPLES):
        assert x["input"] == ex["description"]
        assert x["demo_clock"] == "2026-11-04"
        assert x["shipped_answer"] == x["run"]["generation"]["shipped_text"]   # unedited
        assert x["models"] == ["gpt-6.1-sol"]
        assert x["spend_usd"] == pytest.approx(
            sum(e["cost_usd"] for e in t.entries if e["ref"] == f"{x['id']}#0"))
    demo_calls = [e for e in t.entries if e["phase"] == "demo"]
    assert saved["spend_usd"] == pytest.approx(sum(e["cost_usd"] for e in demo_calls))
    assert {e["stage"] for e in demo_calls} == {"extract", "match", "generate"}
    assert ("match", "X03#0") not in {(e["stage"], e["ref"]) for e in demo_calls}
    assert saved["examples"][2]["statuses"]["risk_tier"] == "not_in_force"


def test_the_output_page_shows_each_answer_and_the_two_notes(tmp_path) -> None:
    out, _, _ = record(tmp_path)
    page = (tmp_path / "demo" / "recording.md").read_text(encoding="utf-8")
    for x in out["examples"]:
        assert x["input"] in page and x["shipped_answer"] in page
    assert page.count("> **Note.**") == 2
    assert EXAMPLES[3]["note"] in page and EXAMPLES[4]["note"] in page
    assert "not an evaluation" in page
    # Each example ends with its footer: recording date, model, its cost, unedited output.
    blocks = page.split("\n---\n")[1:]
    assert len(blocks) == 5
    for block, x in zip(blocks, out["examples"]):
        footer = block.strip().splitlines()[-1]
        assert footer == (f"*Recorded 2026-11-04 · model gpt-6.1-sol · "
                          f"cost ${x['spend_usd']:.4f} · unedited output*")


def test_the_demo_is_recorded_once(tmp_path) -> None:
    _, t, fake = record(tmp_path)
    calls = len(fake.calls)
    with pytest.raises(demo.DemoAlreadyRecorded):
        demo.run_demo(runner.Stages.frozen(runner.RecordingClient(fake)), t,
                      out_dir=tmp_path / "demo", heldout_summary=tmp_path / "e2e_heldout.json")
    assert len(fake.calls) == calls


def test_a_crashed_recording_cannot_be_redone(tmp_path) -> None:
    (tmp_path / "demo").mkdir()
    (tmp_path / "demo" / "recording.started").write_text("{}", encoding="utf-8")
    fake = FakeOpenAI(fake_cases(), verdicts=insufficient)
    with pytest.raises(demo.DemoAlreadyRecorded):
        record(tmp_path, fake)
    assert fake.calls == []


def test_the_demo_waits_for_the_heldout_pass(tmp_path) -> None:
    fake = FakeOpenAI(fake_cases(), verdicts=insufficient)
    with pytest.raises(RuntimeError, match="after the held-out pass"):
        record(tmp_path, fake, heldout_done=False)
    assert fake.calls == [] and not (tmp_path / "demo" / "recording.started").exists()


def test_only_the_frozen_configuration_records(tmp_path) -> None:
    fake = FakeOpenAI(fake_cases(), verdicts=insufficient)
    s = runner.Stages.frozen(runner.RecordingClient(fake))
    s.generator.effort = "medium"
    (tmp_path / "e2e_heldout.json").write_text("{}", encoding="utf-8")
    t = E2ESpend(tmp_path / "spend.jsonl", eval_clock=runner.EVAL_CLOCK)
    with pytest.raises(RuntimeError, match="frozen configuration"):
        demo.run_demo(s, t, out_dir=tmp_path / "demo",
                      heldout_summary=tmp_path / "e2e_heldout.json")
    assert fake.calls == []


def test_demo_calls_stay_under_the_8_dollar_cap(tmp_path) -> None:
    t = E2ESpend(tmp_path / "spend.jsonl", eval_clock=runner.EVAL_CLOCK)
    t.record("heldout", "match", "N01#0", "m", 0, 770_000)           # $7.70 spent
    (tmp_path / "e2e_heldout.json").write_text("{}", encoding="utf-8")
    fake = FakeOpenAI(fake_cases(), verdicts=insufficient)
    out = demo.run_demo(runner.Stages.frozen(runner.RecordingClient(fake)), t,
                        out_dir=tmp_path / "demo", heldout_summary=tmp_path / "e2e_heldout.json")
    assert out["budget_stop"] and t.spent <= 8.0


# --------------------------------------------------------------------------
# The published page, re-rendered at $0 from the saved recording
# --------------------------------------------------------------------------

RECORDING = ROOT / "data/demo/recording.json"


def test_rerender_changes_only_our_annotations_never_the_outputs(tmp_path) -> None:
    before = RECORDING.read_bytes()
    page = demo.rerender_page(RECORDING, ROOT / "data/demo/examples.json", tmp_path / "p.md")
    assert RECORDING.read_bytes() == before                     # the recording is read only
    recording = json.loads(before)
    for x in recording["examples"]:
        assert x["shipped_answer"] in page                      # outputs, unchanged
    assert "## X05: Document completeness check" in page
    assert "a cautious unresolved" not in page.split("## X05")[1]
    assert EXAMPLES[4]["note"] in page
    assert "revised after the recording (2026-11-04)" in page
    assert recording["examples"][4]["covers"] == "a cautious unresolved"   # as recorded


def test_the_committed_page_is_the_rerendered_one(tmp_path) -> None:
    page = demo.rerender_page(RECORDING, ROOT / "data/demo/examples.json", tmp_path / "p.md")
    assert (ROOT / "data/demo/recording.md").read_text(encoding="utf-8") == page + "\n"


def test_rerender_refuses_if_an_input_changed(tmp_path) -> None:
    data = json.loads((ROOT / "data/demo/examples.json").read_text(encoding="utf-8"))
    data["examples"][0]["description"] += " Edited."
    (tmp_path / "e.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="differs from the recording"):
        demo.rerender_page(RECORDING, tmp_path / "e.json", tmp_path / "p.md")

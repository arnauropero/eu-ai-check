"""Phase 5 demo recording (`eu-ai-check eval-e2e --mode demo`).

Five new examples (`data/demo/examples.json`) run once through the frozen pipeline,
exactly as in the held-out pass, and are saved unedited. A demo is not an
evaluation: the examples have no labels and nothing is scored.

- **Recorded once.** A marker is written before the first call. A second recording,
  or a recording after a crash, is refused (`DemoAlreadyRecorded`).
- **After the held-out pass only** (design §7). It runs under the same $8 cap, and
  every call goes to the Phase 5 ledger with phase `demo`, plus its stage
  (extract / match / generate).
- **Its own pinned clock**, `DEMO_CLOCK` = 2026-11-04, the recording date. The
  scored-path clock `runner.EVAL_CLOCK` is not touched. Each example's own date is
  explicit in its description and is not after the demo clock.
- **Saved per example:** the input, the pinned date, the unedited shipped answer
  (plus the raw model output and whether it fell back to the template), the
  statuses, the models that served each call and the example's spend. The page
  `data/demo/recording.md` shows them, with each example's note pointing to the
  matching limitation.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from . import runner
from .spend import BudgetExceeded, E2ESpend

DEMO_CLOCK = date(2026, 11, 4)
EXAMPLES = Path("data/demo/examples.json")
OUT_DIR = Path("data/demo")
MARKER = "recording.started"
RECORDING = "recording.json"
PAGE = "recording.md"


class DemoAlreadyRecorded(RuntimeError):
    pass


def load_examples(path: Path = EXAMPLES) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if date.fromisoformat(data["demo_clock"]) != DEMO_CLOCK:
        raise ValueError("examples file demo_clock differs from DEMO_CLOCK")
    return [{**e, "assessment_date": DEMO_CLOCK.isoformat()} for e in data["examples"]]


def _example_record(example: dict, run: runner.CaseRun, ledger: list[dict]) -> dict[str, Any]:
    calls = [e for e in ledger if e["ref"] == f"{example['id']}#0"]
    g = run.generation or {}
    return {
        "id": example["id"], "covers": example["covers"], "note": example["note"],
        "input": example["description"], "demo_clock": DEMO_CLOCK.isoformat(),
        "relevant_date": run.relevant_date, "relevant_date_basis": run.relevant_date_basis,
        "stage_failed": run.stage_failed, "error": run.error,
        "statuses": run.predicted,
        "citations": run.risk_tier_detail,
        "shipped_answer": g.get("shipped_text"),
        "fallback": g.get("fallback"),
        "raw_model_output": g.get("raw"),
        "models": sorted({e["model"] for e in calls if e["model"]}),
        "calls": [{k: e.get(k) for k in ("seq", "stage", "model", "input_tokens", "output_tokens",
                                         "cost_usd", "error", "attempt", "response_id")}
                  for e in calls],
        "spend_usd": round(sum(e["cost_usd"] for e in calls), 6),
        "run": run.record(),
    }


def render_page(recording: dict[str, Any]) -> str:
    lines = ["# EU AI Check — demo recording", "",
             f"Recorded once on {recording['demo_clock']} with the frozen pipeline "
             f"(`{recording['model']}` for every stage). The answers below are shown exactly "
             "as produced, unedited. This is a demonstration, not an evaluation: the "
             "examples have no labels and are not scored. The legal text is the frozen "
             "2024-08-01 corpus snapshot. See the README for results and limitations.", "",
             f"Total demo spend: ${recording['spend_usd']:.4f}.", ""]
    for x in recording["examples"]:
        lines += ["---", "", f"## {x['id']}: {x['covers']}", "", f"**Input:** {x['input']}", ""]
        if x["note"]:
            lines += [f"> **Note.** {x['note']}", ""]
        if x["shipped_answer"] is None:
            lines += [f"*No answer shipped: {x['stage_failed']}: {x['error']}*", ""]
        else:
            lines += ["**Answer:**", "", "```text", x["shipped_answer"], "```", ""]
        # Footer, at the bottom of each example.
        lines += [f"*Recorded {x['demo_clock']} · model {', '.join(x['models']) or 'none'} · "
                  f"cost ${x['spend_usd']:.4f} · unedited output"
                  + (" (template fallback)" if x["fallback"] else "") + "*", ""]
    return "\n".join(lines)


def run_demo(stages: runner.Stages, tracker: E2ESpend, *, examples_path: Path = EXAMPLES,
             out_dir: Path = OUT_DIR,
             heldout_summary: Path = runner.HELDOUT_SUMMARY) -> dict[str, Any]:
    stages.check_frozen()
    marker, rec_path, page_path = out_dir / MARKER, out_dir / RECORDING, out_dir / PAGE
    if marker.exists() or rec_path.exists() or page_path.exists() or tracker.calls("demo"):
        raise DemoAlreadyRecorded("the demo has already been recorded; it is recorded once")
    if not heldout_summary.exists():
        raise RuntimeError("the demo is recorded only after the held-out pass")
    examples = load_examples(examples_path)
    env = runner._Env()
    out_dir.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"demo_clock": DEMO_CLOCK.isoformat(),
                                  **runner._meta(stages)}, default=str) + "\n",
                      encoding="utf-8")
    spent_before = tracker.spent
    records = []
    budget_stop = None
    for example in examples:
        if budget_stop:
            run = runner.CaseRun(example["id"], "demo", 0, example["assessment_date"], "budget",
                                 f"not called: {budget_stop}")
        else:
            try:
                run = runner.run_case(example, "demo", 0, "demo", stages, tracker, env.corpus,
                                      env.pipeline, env.texts, clock=DEMO_CLOCK)
            except BudgetExceeded as exc:
                budget_stop = str(exc)
                run = runner.CaseRun(example["id"], "demo", 0, example["assessment_date"],
                                     "budget", f"not called: {budget_stop}")
        records.append(_example_record(example, run, tracker.entries))
    recording = {
        "demo_clock": DEMO_CLOCK.isoformat(), "model": runner.MODEL,
        "spend_usd": round(tracker.spent - spent_before, 6),
        "spent_total_usd": round(tracker.spent, 6), "budget_stop": budget_stop,
        **runner._meta(stages), "examples": records,
    }
    rec_path.write_text(json.dumps(recording, indent=2, ensure_ascii=False, default=str) + "\n",
                        encoding="utf-8")
    page_path.write_text(render_page(recording) + "\n", encoding="utf-8")
    return recording


__all__ = ["DEMO_CLOCK", "DemoAlreadyRecorded", "load_examples", "render_page", "run_demo"]

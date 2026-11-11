"""`eu-ai-check assess`: the full frozen pipeline on your own description.

The same code path as the Phase 5 evaluation (`runner.run_case` with the frozen
stages): extraction, provision matching, rules, then generation with checks C0-C8
and the template fallback. It writes nothing. No evaluation ledger, result or label
is touched: spend is tracked in memory for this run only.

**Cost cap.** Before the first call, the run's worst case is computed: each of the
three calls at its full output budget, plus a bound on its input. If that worst
case is over `max_cost`, nothing is called. During the run, every call is guarded
again with its real input, so the run can never spend more than `max_cost`.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any

from ..determination.scopes import candidates
from ..generation.spend import SpendTracker
from . import runner
from .spend import BudgetExceeded, StageView, cost

#: Default cap. A typical assessment costs about $0.08, but the guard has to cover
#: every call at its full output budget (about $0.60 in total), so the cap must be
#: above that or no run could start.
DEFAULT_MAX_COST_USD = 0.75
#: Bound on the generation request size, in characters. Over twice the largest
#: generation input ever recorded (9,296 tokens) at 3 characters per token.
GENERATION_INPUT_CHARS_BOUND = 60_000
#: Far future date: the largest candidate set, so the matcher bound holds for any
#: date the extraction might return.
_LATEST = date(2100, 1, 1)

LIMITATIONS_POINTER = ("Not legal advice. Read the limitations before relying on this "
                       "answer: docs/limitations.md.")


class CostCapExceeded(RuntimeError):
    pass


class AssessSpend:
    """In-memory spend for one assessment, capped at `max_cost`. Same interface as
    the Phase 5 tracker (`guard`, `record`, `view`), so `run_case` runs unchanged."""

    def __init__(self, max_cost: float):
        self.max_cost = max_cost
        self.entries: list[dict[str, Any]] = []

    @property
    def spent(self) -> float:
        return sum(e["cost_usd"] for e in self.entries)

    def guard(self, phase: str, stage: str, input_chars: int, max_output_tokens: int) -> None:
        bound = SpendTracker.worst_case(input_chars, max_output_tokens)
        if self.spent + bound > self.max_cost:
            raise BudgetExceeded(
                f"{stage} call refused: spent ${self.spent:.4f} + worst case ${bound:.4f} "
                f"> --max-cost ${self.max_cost:.2f}")

    def record(self, phase: str, stage: str, ref: str, model: str | None, input_tokens: int,
               output_tokens: int, error: str | None = None, **audit) -> dict:
        entry = {"stage": stage, "model": model, "input_tokens": input_tokens,
                 "output_tokens": output_tokens,
                 "cost_usd": round(cost(input_tokens, output_tokens), 6), "error": error,
                 "attempt": audit.get("attempt")}
        self.entries.append(entry)
        return entry

    def view(self, stage: str, client=None) -> StageView:
        return StageView(self, stage, client)


def worst_case(stages: runner.Stages, description: str, on: date, provisions) -> float:
    x = stages.extractor.request(description, on)
    m = stages.matcher.request(description, candidates(provisions, _LATEST))
    g = stages.generator
    return (SpendTracker.worst_case(runner._request_chars(x), x["max_output_tokens"])
            + SpendTracker.worst_case(runner._request_chars(m), m["max_output_tokens"])
            + SpendTracker.worst_case(GENERATION_INPUT_CHARS_BOUND, g.max_output_tokens))


def assess(description: str, on: date, stages: runner.Stages, *,
           max_cost: float = DEFAULT_MAX_COST_USD) -> dict[str, Any]:
    """One assessment. `on` is the assessment date; a date stated in the
    description takes precedence, as everywhere in the pipeline."""
    stages.check_frozen()
    env = runner._Env()
    bound = worst_case(stages, description, on, env.corpus.provisions)
    if bound > max_cost:
        raise CostCapExceeded(
            f"refused before any call: worst case ${bound:.2f} > --max-cost ${max_cost:.2f} "
            f"(a typical run costs about $0.08; raise --max-cost to at least "
            f"${math.ceil(bound * 100) / 100:.2f})")
    tracker = AssessSpend(max_cost)
    case = {"id": "assess", "description": description, "assessment_date": on.isoformat()}
    try:
        run = runner.run_case(case, "assess", 0, "assess", stages, tracker, env.corpus,
                              env.pipeline, env.texts, clock=on)
        budget_stop = None
    except BudgetExceeded as exc:
        run, budget_stop = None, str(exc)
    g = (run.generation or {}) if run else {}
    return {
        "answer": g.get("shipped_text"),
        "fallback": g.get("fallback"),
        "stage_failed": run.stage_failed if run else "budget",
        "error": run.error if run else budget_stop,
        "relevant_date": run.relevant_date if run else None,
        "statuses": run.predicted if run else {},
        "worst_case_usd": round(bound, 4),
        "max_cost_usd": max_cost,
        "cost_usd": round(tracker.spent, 6),
        "calls": tracker.entries,
    }


def render(result: dict[str, Any]) -> str:
    lines = []
    if result["answer"] is None:
        lines.append(f"No answer: {result['stage_failed']}: {result['error']}")
    else:
        lines.append(result["answer"])
        if result["fallback"]:
            lines.append("\n(The model's text failed a check, so the plain template answer "
                         "is shown.)")
    calls = result["calls"]
    lines += ["", "---",
              f"Assessed for {result['relevant_date'] or 'n/a'} with the frozen pipeline "
              f"({runner.MODEL}).",
              f"Cost of this run: ${result['cost_usd']:.4f} ({len(calls)} API call"
              f"{'s' if len(calls) != 1 else ''}; worst case checked before the run "
              f"${result['worst_case_usd']:.2f}, cap --max-cost ${result['max_cost_usd']:.2f}).",
              LIMITATIONS_POINTER]
    return "\n".join(lines)

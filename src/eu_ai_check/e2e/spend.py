"""Phase 5 spend tracker (SYSTEM_SPEC.md §18, Phase 5 pre-registration).

The Phase 4 tracker (`generation/spend.py`) stays as frozen for Phase 4. This one
reuses its prices and worst-case bound and adds what Phase 5 needs:

- one cap, $8, for every Phase 5 API call, enforced in code;
- the held-out reserve, $3.48 (20 cases × $0.116, the dearest case observed, × 1.5),
  held back from every other call until the held-out pass has started, so nothing
  can leave the pass unable to run;
- the stability experiment's own hard cap, $1.50 (raised from $1.10 before freezing
  and before any API call: the per-call worst case would have cut round 3 short);
- every call records its stage (extract / match / generate).

Before every call, `guard()` raises if the call's worst case (input at 3 chars per
token plus the full output budget) could breach a cap. Every call, failed ones too,
is appended to `data/eval/results/phase5_spend.jsonl`, and spend is recomputed from
that ledger, so it persists across runs.

The tracker never reads the system clock: a ledger line carries a sequence number,
the pinned evaluation clock and the API's own `created_at`, not a local timestamp.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from ..generation.spend import BudgetExceeded, SpendTracker, cost

__all__ = ["BudgetExceeded", "E2ESpend", "StageView", "cost"]

CAP_USD = 8.00
STABILITY_CAP_USD = 1.50             # raised from $1.10 before freezing (§18)
HELDOUT_CASES = 20
MAX_CASE_COST_OBSERVED_USD = 0.116      # extraction + matcher + generation maxima (§6)
RESERVE_FACTOR = 1.5
HELDOUT_RESERVE_USD = 3.48              # = 20 × 0.116 × 1.5, tied by a test

LEDGER = Path("data/eval/results/phase5_spend.jsonl")
PHASES = ("stability", "heldout", "demo")
STAGES = ("extract", "match", "generate")


class E2ESpend:
    def __init__(self, ledger: Path = LEDGER, *, eval_clock: date):
        self.ledger = ledger
        self.eval_clock = eval_clock
        self.entries: list[dict] = []
        if ledger.exists():
            self.entries = [json.loads(l) for l in ledger.read_text(encoding="utf-8").splitlines()
                            if l.strip()]

    # -- state -------------------------------------------------------------
    @property
    def spent(self) -> float:
        return sum(e["cost_usd"] for e in self.entries)

    def spent_in(self, phase: str) -> float:
        return sum(e["cost_usd"] for e in self.entries if e["phase"] == phase)

    def calls(self, phase: str) -> int:
        return sum(1 for e in self.entries if e["phase"] == phase)

    def reserve(self) -> float:
        """Held back for the held-out pass until its first call."""
        return HELDOUT_RESERVE_USD if self.calls("heldout") == 0 else 0.0

    def budget_left(self, phase: str) -> float:
        left = CAP_USD - self.spent - (0.0 if phase == "heldout" else self.reserve())
        if phase == "stability":
            left = min(left, STABILITY_CAP_USD - self.spent_in("stability"))
        return left

    # -- gate ----------------------------------------------------------------
    def guard(self, phase: str, stage: str, input_chars: int, max_output_tokens: int) -> None:
        if phase not in PHASES:
            raise ValueError(f"unknown phase {phase!r}")
        if stage not in STAGES:
            raise ValueError(f"unknown stage {stage!r}")
        bound = SpendTracker.worst_case(input_chars, max_output_tokens)
        held = 0.0 if phase == "heldout" else self.reserve()
        if self.spent + bound + held > CAP_USD:
            raise BudgetExceeded(
                f"{phase}/{stage} call refused: spent ${self.spent:.4f} + worst case "
                f"${bound:.4f} + held-out reserve ${held:.4f} > cap ${CAP_USD:.2f}")
        if phase == "stability" and self.spent_in("stability") + bound > STABILITY_CAP_USD:
            raise BudgetExceeded(
                f"stability/{stage} call refused: stability spent "
                f"${self.spent_in('stability'):.4f} + worst case ${bound:.4f} > stability cap "
                f"${STABILITY_CAP_USD:.2f}")

    def preflight_heldout(self) -> None:
        """Before the single held-out pass: no held-out call may exist yet, and the
        reserve must still be there in full."""
        if self.calls("heldout"):
            raise BudgetExceeded("held-out calls already recorded: the pass runs exactly once")
        if CAP_USD - self.spent < HELDOUT_RESERVE_USD:
            raise BudgetExceeded(f"held-out reserve not available: spent ${self.spent:.4f}")

    # -- log ---------------------------------------------------------------
    def record(self, phase: str, stage: str, ref: str, model: str | None, input_tokens: int,
               output_tokens: int, error: str | None = None, **audit) -> dict:
        c = cost(input_tokens, output_tokens)
        entry = {
            "seq": len(self.entries) + 1, "eval_clock": self.eval_clock.isoformat(),
            "phase": phase, "stage": stage, "ref": ref, "model": model,
            "input_tokens": input_tokens, "output_tokens": output_tokens,
            "cost_usd": round(c, 6), "cumulative_usd": round(self.spent + c, 6),
            "error": error, **audit,
        }
        self.entries.append(entry)
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        with self.ledger.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
        return entry

    def view(self, stage: str, client=None) -> StageView:
        return StageView(self, stage, client)


class StageView:
    """The Phase 4 tracker interface (`guard`, `record`) bound to one stage, so the
    Phase 4 generation harness (`generation.evaluation.evaluate_item`) runs unchanged
    against the Phase 5 ledger and caps."""

    def __init__(self, tracker: E2ESpend, stage: str, client=None):
        self.tracker, self.stage, self.client = tracker, stage, client

    def guard(self, phase: str, input_chars: int, max_output_tokens: int) -> None:
        self.tracker.guard(phase, self.stage, input_chars, max_output_tokens)

    def record(self, phase: str, ref: str, model: str | None, input_tokens: int,
               output_tokens: int, error: str | None = None, **audit) -> dict:
        last = getattr(self.client, "last", None)
        audit.setdefault("api_created_at", getattr(last, "created_at", None))
        return self.tracker.record(phase, self.stage, ref, model, input_tokens, output_tokens,
                                   error, **audit)

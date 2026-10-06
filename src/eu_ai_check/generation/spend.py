"""Phase 4 spend tracker (pre-registration 33cf62c, SYSTEM_SPEC.md §18).

- The cap is the pre-registered $5 for all Phase 4 API calls. The constants below are
  tied to the frozen §18 text by a test.
- The held-out pass is reserved first. Before it starts, the reserve is the largest
  of: the pre-registered $1.95 (39 calls × $0.05), 39 × the dearest call observed so
  far, and the held-out pass's own worst case (each held-out input's size plus the
  full output budget). The reserve can only be stricter than the plan, never looser,
  so dev iteration can never leave the held-out pass unable to run in full.
- Before every call, `guard()` aborts hard (raises) if the call's worst-case cost
  would breach the cap: worst-case input plus the full output budget, plus the
  reserve for any non-held-out call.
- Every call, including a failed one, is appended to a JSONL ledger with its
  tokens and cost. Spend is recomputed from that ledger, so it persists across runs.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

PREREGISTERED_CAP_USD = 5.00
PRICE_INPUT_PER_M = 2.0     # gpt-6.1-sol, USD per million input tokens
PRICE_OUTPUT_PER_M = 10.0   # USD per million output tokens
HELDOUT_CALLS = 39
PLANNING_PRICE_PER_CALL = 0.05
PREREGISTERED_RESERVE_USD = HELDOUT_CALLS * PLANNING_PRICE_PER_CALL   # $1.95
#: Conservative chars-per-token for the worst-case input estimate (English text
#: runs about 4; 3 over-estimates on purpose).
CHARS_PER_TOKEN_BOUND = 3.0

LEDGER = Path("data/eval/results/phase4_spend.jsonl")
PHASES = ("smoke", "dev", "heldout")


class BudgetExceeded(RuntimeError):
    """Raised before a call that could breach the pre-registered cap."""


def cost(input_tokens: int, output_tokens: int) -> float:
    return input_tokens * PRICE_INPUT_PER_M / 1e6 + output_tokens * PRICE_OUTPUT_PER_M / 1e6


class SpendTracker:
    def __init__(self, ledger: Path = LEDGER, cap: float = PREREGISTERED_CAP_USD,
                 heldout_worst_case: float = 0.0):
        self.ledger = ledger
        self.cap = cap
        self.heldout_worst_case = heldout_worst_case
        self.entries: list[dict] = []
        if ledger.exists():
            self.entries = [json.loads(l) for l in ledger.read_text(encoding="utf-8").splitlines()
                            if l.strip()]

    # -- state -------------------------------------------------------------
    @property
    def spent(self) -> float:
        return sum(e["cost_usd"] for e in self.entries)

    def calls(self, phase: str) -> int:
        return sum(1 for e in self.entries if e["phase"] == phase)

    def per_call_estimate(self) -> float:
        observed = [e["cost_usd"] for e in self.entries if e["cost_usd"] > 0]
        return max([PLANNING_PRICE_PER_CALL, *observed])

    def reserve(self) -> float:
        remaining = max(0, HELDOUT_CALLS - self.calls("heldout"))
        if remaining < HELDOUT_CALLS:
            return remaining * self.per_call_estimate()   # the pass is under way
        return max(PREREGISTERED_RESERVE_USD, remaining * self.per_call_estimate(),
                   self.heldout_worst_case)

    @staticmethod
    def worst_case(input_chars: int, max_output_tokens: int) -> float:
        return cost(math.ceil(input_chars / CHARS_PER_TOKEN_BOUND), max_output_tokens)

    def dev_budget_left(self) -> float:
        return self.cap - self.spent - self.reserve()

    # -- gate ----------------------------------------------------------------
    def guard(self, phase: str, input_chars: int, max_output_tokens: int) -> None:
        if phase not in PHASES:
            raise ValueError(f"unknown phase {phase!r}")
        bound = self.worst_case(input_chars, max_output_tokens)
        held = 0.0 if phase == "heldout" else self.reserve()
        if self.spent + bound + held > self.cap:
            raise BudgetExceeded(
                f"{phase} call refused: spent ${self.spent:.4f} + worst case ${bound:.4f}"
                f" + held-out reserve ${held:.4f} > cap ${self.cap:.2f}")

    def preflight_heldout(self, max_output_tokens: int, input_chars: list[int]) -> None:
        """Before the single held-out pass: refuse to start unless the whole pass
        fits under the cap at its worst case, so it can never stop half-way."""
        remaining = HELDOUT_CALLS - self.calls("heldout")
        if remaining != HELDOUT_CALLS:
            raise BudgetExceeded("held-out calls already recorded: the pass runs exactly once")
        worst = sum(self.worst_case(c, max_output_tokens) for c in input_chars)
        if self.spent + worst > self.cap:
            raise BudgetExceeded(
                f"held-out pass refused: spent ${self.spent:.4f} + worst case ${worst:.4f}"
                f" > cap ${self.cap:.2f}")

    # -- log ---------------------------------------------------------------
    def record(self, phase: str, ref: str, model: str | None, input_tokens: int,
               output_tokens: int, error: str | None = None) -> dict:
        c = cost(input_tokens, output_tokens)
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "phase": phase, "ref": ref, "model": model,
            "input_tokens": input_tokens, "output_tokens": output_tokens,
            "cost_usd": round(c, 6), "cumulative_usd": round(self.spent + c, 6),
            "error": error,
        }
        self.entries.append(entry)
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        with self.ledger.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        return entry

"""Phase 5 spend tracker: $8 cap, held-out reserve first, $1.50 stability cap,
hard abort before any call that could breach a cap, ledger without wall clock."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from eu_ai_check.e2e import spend
from eu_ai_check.e2e.spend import BudgetExceeded, E2ESpend, cost

CLOCK = date(2026, 10, 25)
SPEC = Path("SYSTEM_SPEC.md").read_text(encoding="utf-8")


def tracker(tmp_path) -> E2ESpend:
    return E2ESpend(tmp_path / "l.jsonl", eval_clock=CLOCK)


def test_constants_match_the_preregistration() -> None:
    assert spend.CAP_USD == 8.00
    assert spend.STABILITY_CAP_USD == 1.50
    assert spend.HELDOUT_RESERVE_USD == pytest.approx(3.48)
    assert round(spend.HELDOUT_CASES * spend.MAX_CASE_COST_OBSERVED_USD
                 * spend.RESERVE_FACTOR, 2) == spend.HELDOUT_RESERVE_USD
    assert "**Spend (binding).** One cap, **$8**, for every Phase 5 API call" in SPEC
    assert "held-out reserve of **$3.48** (20 cases × $0.116 × 1.5)" in SPEC
    assert "stability experiment has its own hard cap of **$1.50**" in SPEC


def test_reserve_is_held_first_until_the_heldout_pass_starts(tmp_path) -> None:
    t = tracker(tmp_path)
    assert t.reserve() == pytest.approx(3.48)
    assert t.budget_left("demo") == pytest.approx(8 - 3.48)
    t.record("heldout", "extract", "N01#0", "m", 1000, 500)
    assert t.reserve() == 0.0


def test_non_heldout_call_may_never_eat_the_reserve(tmp_path) -> None:
    t = tracker(tmp_path)
    for i in range(44):
        t.record("demo", "match", f"X{i}", "m", 0, 10_000)        # $0.10 each = $4.40
    t.guard("demo", "generate", 3_000, 1_000)                      # $4.41 + $3.48 <= $8
    with pytest.raises(BudgetExceeded):
        t.guard("demo", "match", 30_000, 32_000)                   # + $0.34 > $8
    t.guard("heldout", "match", 30_000, 32_000)                    # the reserve is for this


def test_cap_is_never_breached_even_by_heldout(tmp_path) -> None:
    t = tracker(tmp_path)
    t.record("heldout", "match", "N01#0", "m", 0, 790_000)         # $7.90
    with pytest.raises(BudgetExceeded):
        t.guard("heldout", "match", 3_000, 32_000)


def test_stability_cap_uses_the_worst_case_of_each_call(tmp_path) -> None:
    t = tracker(tmp_path)
    for i in range(11):
        t.record("stability", "match", f"S{i}", "m", 0, 7_800)     # $0.078 each = $0.858
    # The last matcher call of round 3 at mean cost: $0.858 + worst $0.34 fits $1.50.
    t.guard("stability", "match", 33_000, 32_000)
    t.record("stability", "match", "S11", "m", 0, 31_000)          # $1.168
    with pytest.raises(BudgetExceeded, match="stability cap"):
        t.guard("stability", "match", 33_000, 32_000)


def test_raised_stability_cap_lets_the_planned_round_3_finish_where_1_10_would_not(tmp_path) -> None:
    worst_matcher = spend.SpendTracker.worst_case(33_000, 32_000)
    spent_before_last_matcher = 11 * 0.078
    assert spent_before_last_matcher + worst_matcher > 1.10
    assert spent_before_last_matcher + worst_matcher <= spend.STABILITY_CAP_USD


def test_unknown_phase_or_stage_is_refused(tmp_path) -> None:
    t = tracker(tmp_path)
    with pytest.raises(ValueError):
        t.guard("dev", "match", 1, 1)
    with pytest.raises(ValueError):
        t.guard("stability", "retrieve", 1, 1)


def test_preflight_refuses_a_second_heldout_pass(tmp_path) -> None:
    t = tracker(tmp_path)
    t.preflight_heldout()
    t.record("heldout", "extract", "N01#0", "m", 1000, 500)
    with pytest.raises(BudgetExceeded, match="exactly once"):
        t.preflight_heldout()


def test_ledger_persists_records_stage_and_never_the_wall_clock(tmp_path) -> None:
    t = tracker(tmp_path)
    t.record("stability", "match", "D32#0", "m", 8218, 4077, response_id="r1",
             api_created_at=1761350400)
    line = json.loads((tmp_path / "l.jsonl").read_text(encoding="utf-8"))
    assert line["stage"] == "match" and line["seq"] == 1
    assert line["eval_clock"] == "2026-10-25"
    assert "ts" not in line
    assert line["cost_usd"] == pytest.approx(cost(8218, 4077), abs=1e-6)
    again = tracker(tmp_path)
    assert again.spent == pytest.approx(t.spent)
    assert again.spent_in("stability") == pytest.approx(t.spent)

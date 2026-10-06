"""Spend tracker: pre-registered cap, held-out reserved first, hard abort, ledger."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eu_ai_check.generation import spend
from eu_ai_check.generation.spend import BudgetExceeded, SpendTracker, cost


def test_constants_match_the_frozen_preregistration() -> None:
    text = Path("SYSTEM_SPEC.md").read_text(encoding="utf-8")
    assert "**Spend cap (APPROVED): $5 for all Phase 4 API calls.**" in text
    assert "$2 / $10 per million input / output tokens" in text
    assert "The held-out pass (39 calls, ≤ $1.95 at the $0.05 planning price)" in text
    assert spend.PREREGISTERED_CAP_USD == 5.0
    assert (spend.PRICE_INPUT_PER_M, spend.PRICE_OUTPUT_PER_M) == (2.0, 10.0)
    assert spend.HELDOUT_CALLS == 39
    assert spend.PREREGISTERED_RESERVE_USD == pytest.approx(1.95)


def test_cost_reproduces_the_phase_3_matcher_call() -> None:
    assert cost(8218, 4077) == pytest.approx(0.0572, abs=1e-4)


def test_reserve_is_held_first_and_never_below_plan(tmp_path) -> None:
    t = SpendTracker(tmp_path / "l.jsonl")
    assert t.reserve() == pytest.approx(1.95)
    t2 = SpendTracker(tmp_path / "l2.jsonl", heldout_worst_case=2.39)
    assert t2.reserve() == pytest.approx(2.39)


def test_dear_calls_grow_the_reserve(tmp_path) -> None:
    t = SpendTracker(tmp_path / "l.jsonl")
    t.record("dev", "D01#0", "m", 10_000, 9_000)        # $0.11
    assert t.reserve() == pytest.approx(39 * cost(10_000, 9_000))


def test_dev_call_is_refused_before_it_could_eat_the_reserve(tmp_path) -> None:
    t = SpendTracker(tmp_path / "l.jsonl")
    for i in range(55):
        t.record("dev", f"D{i}", "m", 4000, 2600)        # $0.034 each, ~$1.87
    t.guard("dev", 15_000, 5000)                          # still fits
    for i in range(35):
        t.record("dev", f"X{i}", "m", 4000, 2600)        # 90 calls, ~$3.06 spent
    # $3.06 + worst case $0.06 + reserve $1.95 > $5: refused.
    with pytest.raises(BudgetExceeded):
        t.guard("dev", 15_000, 5000)
    t.guard("heldout", 15_000, 5000)                      # the reserve is for held-out


def test_cap_is_never_breached_even_by_heldout(tmp_path) -> None:
    t = SpendTracker(tmp_path / "l.jsonl")
    for i in range(50):
        t.record("dev", f"D{i}", "m", 0, 9_920)           # $0.0992 each = $4.96
    t.guard("heldout", 3_000, 1_000)                      # $4.96 + $0.012 fits
    with pytest.raises(BudgetExceeded):
        t.guard("heldout", 15_000, 5000)                  # $4.96 + $0.06 > $5


def test_preflight_requires_the_whole_heldout_pass_to_fit(tmp_path) -> None:
    t = SpendTracker(tmp_path / "l.jsonl")
    t.preflight_heldout(5000, [18_000] * 39)
    for i in range(30):
        t.record("dev", f"D{i}", "m", 0, 10_000)          # $3.00
    with pytest.raises(BudgetExceeded):
        t.preflight_heldout(5000, [18_000] * 39)


def test_preflight_refuses_a_second_heldout_pass(tmp_path) -> None:
    t = SpendTracker(tmp_path / "l.jsonl")
    t.record("heldout", "D02#0", "m", 4000, 2000)
    with pytest.raises(BudgetExceeded, match="exactly once"):
        t.preflight_heldout(5000, [18_000] * 39)


def test_every_call_is_logged_and_spend_persists(tmp_path) -> None:
    path = tmp_path / "l.jsonl"
    t = SpendTracker(path)
    t.record("smoke", "smoke", "m", 4000, 2500)
    t.record("dev", "D01#0", "m", 0, 0, error="APIConnectionError")
    lines = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
    assert [l["phase"] for l in lines] == ["smoke", "dev"]
    assert lines[0]["cost_usd"] == pytest.approx(0.033)
    assert lines[1]["error"] == "APIConnectionError"
    assert SpendTracker(path).spent == pytest.approx(0.033)


def test_unknown_phase_is_rejected(tmp_path) -> None:
    with pytest.raises(ValueError):
        SpendTracker(tmp_path / "l.jsonl").guard("prod", 1, 1)

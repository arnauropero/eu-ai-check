"""Phase 5 end-to-end eval set: N01-N12 (data/eval/e2e_cases.json) plus H01-H08
(data/eval/determination_heldout_5_1_c.json).

The label checks are the Phase 3 ones (tests/test_determination_eval.py), applied
to both files: every rule-based label must follow from the gold facts and the
labelled risk tier, so a labelling slip fails here instead of becoming "ground
truth".
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import test_determination_eval as phase3
from eu_ai_check.corpus.models import Corpus

ROOT = Path(__file__).resolve().parents[1]
NEW = json.loads((ROOT / "data/eval/e2e_cases.json").read_text(encoding="utf-8"))["cases"]
HELDOUT = json.loads(
    (ROOT / "data/eval/determination_heldout_5_1_c.json").read_text(encoding="utf-8")
)["cases"]
ALL = NEW + HELDOUT
BY_ID = {c["id"]: c for c in ALL}


def test_ids() -> None:
    assert [c["id"] for c in NEW] == [f"N{i:02d}" for i in range(1, 13)]
    assert [c["id"] for c in HELDOUT] == [f"H{i:02d}" for i in range(1, 9)]


def test_no_description_is_reused_from_dev_data() -> None:
    dev = {c["description"] for c in phase3.CASES}
    extraction = json.loads(
        (ROOT / "data/eval/extraction_cases.json").read_text(encoding="utf-8"))["cases"]
    dev |= {c["description"] for c in extraction}
    assert not dev & {c["description"] for c in ALL}


@pytest.mark.parametrize("case", ALL, ids=lambda c: c["id"])
def test_gold_facts_are_valid_extraction_output(case) -> None:
    phase3.test_gold_facts_are_valid_extraction_output(case)


@pytest.mark.parametrize("case", ALL, ids=lambda c: c["id"])
def test_statuses_use_the_four_state_vocabulary(case) -> None:
    phase3.test_statuses_use_the_four_state_vocabulary(case)


def test_every_cited_provision_exists_in_the_corpus() -> None:
    known = {p.logical_provision_id for p in Corpus.load(ROOT / "data/corpus").provisions}
    for case in ALL:
        e = case["expected"]
        cited = (e["risk_tier"]["annex_iii"] + e["risk_tier"]["annex_i"]
                 + e["prohibited_practice"]["points"])
        for pid in cited:
            assert pid in known, (case["id"], pid)


@pytest.mark.parametrize("case", ALL, ids=lambda c: c["id"])
def test_risk_tier_label_is_consistent_with_route_dates(case) -> None:
    phase3.test_risk_tier_label_is_consistent_with_route_dates(case)


@pytest.mark.parametrize("case", ALL, ids=lambda c: c["id"])
def test_rule_based_labels_follow_from_facts_and_risk_tier(case) -> None:
    phase3.test_rule_based_labels_follow_from_facts_and_risk_tier(case)


def test_plan_section_8_coverage_in_the_new_cases() -> None:
    """PROJECT_PLAN.md §8, demonstrated again on cases never run."""
    e = {cid: c["expected"] for cid, c in BY_ID.items()}
    f = {cid: c["gold_facts"] for cid, c in BY_ID.items()}
    # Article 25 transfer to a non-developer, with simultaneous roles (N01).
    assert f["N01"]["develops_system"] is False
    assert e["N01"]["actor_role"]["actor_roles"] == ["provider", "deployer"]
    assert f["N02"]["develops_system"] is False
    assert e["N02"]["actor_role"]["actor_roles"] == ["provider"]
    # Article 6(3) derogation applied.
    assert e["N06"]["risk_tier"]["derogation"] == "applied"
    # The two routes and their dates.
    assert e["N07"]["risk_tier"]["system_category"] == ["article_6_1_high_risk"]
    assert e["N07"]["risk_tier"]["status"] == "not_applicable/not_yet_applicable"
    assert e["N08"]["risk_tier"]["system_category"] == [
        "annex_iii_high_risk", "article_6_1_high_risk"]
    # One description, three dates, three outcomes on Article 26; not_in_force vs
    # not_applicable on the same provision.
    system = {BY_ID[c]["description"].split(" Which ")[0] for c in ("N03", "N04", "N05")}
    assert len(system) == 1
    art26 = [e[c]["obligations"]["aiact:art_26"] for c in ("N05", "N03", "N04")]
    assert art26 == ["not_in_force", "not_applicable/not_yet_applicable", "applicable"]
    # All four states across the 20 cases.
    seen = {e[c]["risk_tier"]["status"].split("/")[0] for c in e}
    assert seen == {"applicable", "not_applicable", "unresolved", "not_in_force"}


RELATIVE_TIME = ("today", "now", "currently", "this year", "next month", "next year",
                 "last month", "last year", "recently")


@pytest.mark.parametrize("case", ALL, ids=lambda c: c["id"])
def test_expected_outputs_do_not_move_with_calendar_time(case) -> None:
    """Every date the labels depend on is fixed in the file: a stated date in the
    description, or the case's own assessment_date. Nothing reads the wall clock."""
    text = case["description"].lower()
    assert not [w for w in RELATIVE_TIME if f" {w}" in f" {text}"], case["id"]
    phase3.date.fromisoformat(case["assessment_date"])


def test_n08_route_statuses_follow_route_dates() -> None:
    rt = BY_ID["N08"]["expected"]["risk_tier"]
    on = phase3._relevant_date(BY_ID["N08"])
    want = {c: ("applicable" if on >= phase3.ROUTE_START[c]
                else "not_applicable/not_yet_applicable") for c in rt["system_category"]}
    assert rt["route_status"] == want
    assert rt["route_status"]["annex_iii_high_risk"] == "applicable"
    assert rt["route_status"]["article_6_1_high_risk"] == "not_applicable/not_yet_applicable"
    assert rt["status"] == "applicable"  # one applicable route is enough (§9.1)


#: SHA-256 of each file's `cases` content, frozen in the Phase 5 pre-registration
#: (SYSTEM_SPEC.md §18, 2026-11-04). Any later change must be flagged post_hoc.
FROZEN_E2E_SHA = "a50afdabda92f3ee3724047d93dcd9d5e329e3adec517f697660a5d5fff8c0d4"
FROZEN_HELDOUT_5_1_C_SHA = "11e564939375d5482dcd53145a5b245507d1b52542e665c69d9cc693ac1d401c"


def _content_sha(cases: list[dict]) -> str:
    import hashlib

    return hashlib.sha256(json.dumps(cases, sort_keys=True, ensure_ascii=False)
                          .encode("utf-8")).hexdigest()


def test_eval_set_is_frozen() -> None:
    assert _content_sha(NEW) == FROZEN_E2E_SHA
    assert _content_sha(HELDOUT) == FROZEN_HELDOUT_5_1_C_SHA
    spec = (ROOT / "SYSTEM_SPEC.md").read_text(encoding="utf-8")
    assert FROZEN_E2E_SHA in spec and FROZEN_HELDOUT_5_1_C_SHA in spec


def test_n04_n07_n08_ask_about_1_october_2026() -> None:
    for cid in ("N04", "N07", "N08"):
        assert "1 October 2026" in BY_ID[cid]["description"], cid
        assert BY_ID[cid]["gold_facts"]["relevant_date"] == "2026-10-01", cid

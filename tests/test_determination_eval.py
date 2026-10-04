"""Phase 3 determination eval set (data/eval/determination_cases.json).

The set is pre-registered: these tests pin its content and check that every
rule-based label (actor roles, Article 16/26 applicability) follows from the gold
facts and the labelled risk tier under SYSTEM_SPEC.md §4.3 / §14.2. A labelling
slip therefore fails here instead of becoming "ground truth".
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import pytest

from eu_ai_check.corpus.models import Corpus
from eu_ai_check.extraction.schema import CaseFacts

ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = ROOT / "data/eval/determination_cases.json"
DATA = json.loads(CASES_PATH.read_text(encoding="utf-8"))
CASES = DATA["cases"]
BY_ID = {c["id"]: c for c in CASES}

#: Canonical SHA-256 of the `cases` list as pre-registered. Changing a case or a
#: label after the first determination run must be flagged post_hoc, not done
#: silently (Phase 1/2 precedent).
PRE_REGISTERED_CASES_SHA = "b060faec5b39dcdca782f926ff8613b8957cdd147674be7622a9211f0d4ed572"

ENTRY_INTO_FORCE = date(2024, 8, 1)
ROUTE_START = {
    "annex_iii_high_risk": date(2026, 8, 2),
    "article_6_1_high_risk": date(2027, 8, 2),
}
STATUSES = {
    "applicable",
    "unresolved",
    "not_in_force",
    "not_applicable/conditions_not_met",
    "not_applicable/not_yet_applicable",
}


def _or(*xs):
    if any(x is True for x in xs):
        return True
    return False if all(x is False for x in xs) else None


def _and(*xs):
    if any(x is False for x in xs):
        return False
    return True if all(x is True for x in xs) else None


def _relevant_date(case) -> date:
    stated = case["gold_facts"]["relevant_date"]
    return date.fromisoformat(stated or case["assessment_date"])


def test_cases_are_pinned_as_pre_registered() -> None:
    digest = hashlib.sha256(
        json.dumps(CASES, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    assert digest == PRE_REGISTERED_CASES_SHA


def test_ids_are_unique_and_sequential() -> None:
    assert [c["id"] for c in CASES] == [f"D{i:02d}" for i in range(1, len(CASES) + 1)]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_gold_facts_are_valid_extraction_output(case) -> None:
    """Gold inputs must be something the Phase 2 extractor could emit."""
    CaseFacts.model_validate(case["gold_facts"])


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_statuses_use_the_four_state_vocabulary(case) -> None:
    e = case["expected"]
    assert e["risk_tier"]["status"] in STATUSES
    assert e["prohibited_practice"]["status"] in STATUSES
    for status in e["obligations"].values():
        assert status in STATUSES


def test_every_cited_provision_exists_in_the_corpus() -> None:
    known = {
        p.logical_provision_id for p in Corpus.load(ROOT / "data/corpus").provisions
    }
    for case in CASES:
        e = case["expected"]
        cited = (
            e["risk_tier"]["annex_iii"]
            + e["risk_tier"]["annex_i"]
            + e["prohibited_practice"]["points"]
            + list(e["obligations"])
        )
        for pid in cited:
            if pid.count(":") == 1:  # article-level obligation group
                assert any(k.startswith(pid + ":") for k in known), (case["id"], pid)
            else:
                assert pid in known, (case["id"], pid)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_risk_tier_label_is_consistent_with_route_dates(case) -> None:
    """§9.1: applicable needs an established route that applies on the date;
    not_yet_applicable needs established routes that all start later."""
    rt = case["expected"]["risk_tier"]
    on = _relevant_date(case)
    cats = rt["system_category"]
    match rt["status"]:
        case "applicable":
            assert any(on >= ROUTE_START[c] for c in cats)
        case "not_applicable/not_yet_applicable":
            assert cats and all(on < ROUTE_START[c] for c in cats)
        case _:
            assert cats == []


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_rule_based_labels_follow_from_facts_and_risk_tier(case) -> None:
    """Recompute roles (§4.3) and Article 16/26 status (§12, §14.2)."""
    f, e = case["gold_facts"], case["expected"]
    on = _relevant_date(case)
    if on < ENTRY_INTO_FORCE:
        assert e["actor_role"] == {
            "actor_roles": [], "unresolved_roles": [], "status": "not_in_force"
        }
        assert set(e["obligations"].values()) == {"not_in_force"}
        return

    rt = e["risk_tier"]
    established = set(rt["system_category"])
    high = True if established else (None if rt["status"] == "unresolved" else False)
    substantial = _or(f["modifies_safety_relevant_components"],
                      f["modifies_intended_purpose"])
    role = {
        "provider": _or(f["develops_system"], _and(high, _or(
            f["puts_own_name_or_trademark"], substantial,
            f["modifies_intended_purpose"]))),
        "deployer": f["uses_system"],
    }
    unresolved = [r for r in ("provider", "deployer") if role[r] is None]
    assert e["actor_role"] == {
        "actor_roles": [r for r in ("provider", "deployer") if role[r] is True],
        "unresolved_roles": unresolved,
        "status": "unresolved" if unresolved else "known",
    }

    def obligation(actor: str) -> str:
        outcomes = []
        for cat, start in ROUTE_START.items():
            category = True if cat in established else (None if high is None else False)
            met = _and(role[actor], category)
            outcomes.append(("applicable" if on >= start else "nya") if met else met)
        if "applicable" in outcomes:
            return "applicable"
        if None in outcomes:
            return "unresolved"
        if "nya" in outcomes:
            return "not_applicable/not_yet_applicable"
        return "not_applicable/conditions_not_met"

    assert e["obligations"]["aiact:art_16"] == obligation("provider")
    assert e["obligations"]["aiact:art_26"] == obligation("deployer")


def test_prohibitions_before_february_2025_are_not_yet_applicable() -> None:
    for case in CASES:
        pp = case["expected"]["prohibited_practice"]
        on = _relevant_date(case)
        if pp["points"] and ENTRY_INTO_FORCE <= on < date(2025, 2, 2):
            assert pp["status"] == "not_applicable/not_yet_applicable", case["id"]


def test_plan_section_8_coverage_cases_are_present() -> None:
    """PROJECT_PLAN.md §8 names cases V1 must demonstrate."""
    e = {cid: c["expected"] for cid, c in BY_ID.items()}
    # Simultaneous provider + deployer.
    assert e["D02"]["actor_role"]["actor_roles"] == ["provider", "deployer"]
    # Article 25 transfer: a non-developer becomes provider.
    assert BY_ID["D35"]["gold_facts"]["develops_system"] is False
    assert "provider" in e["D35"]["actor_role"]["actor_roles"]
    # Article 6(3) derogation applied.
    assert e["D09"]["risk_tier"]["derogation"] == "applied"
    # Two high-risk routes.
    assert e["D19"]["risk_tier"]["system_category"] == ["article_6_1_high_risk"]
    assert e["D01"]["risk_tier"]["system_category"] == ["annex_iii_high_risk"]
    # Same provision, different dates, different outcomes; not_in_force vs
    # not_applicable on one provision.
    art26 = [e[c]["obligations"]["aiact:art_26"] for c in ("D38", "D39", "D01")]
    assert art26 == ["not_in_force", "not_applicable/not_yet_applicable", "applicable"]
    # All four states exercised.
    seen = {e[c]["risk_tier"]["status"].split("/")[0] for c in e}
    assert seen == {"applicable", "not_applicable", "unresolved", "not_in_force"}

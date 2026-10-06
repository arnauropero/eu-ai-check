"""Phase 4 adapter: cached Phase 3 records -> the §16.2 contract. No API call."""

from __future__ import annotations

import json
from datetime import date

import pytest
from generation_fixtures import corpus, load_input, load_synthetic, records

from eu_ai_check.generation.adapter import (
    AdapterError,
    GenerationInput,
    NoMatcher,
    split_status,
)

ALL_REFS = sorted(records())
STATUSES = {"applicable", "not_applicable", "unresolved", "not_in_force"}


@pytest.fixture(scope="module")
def all_inputs() -> dict[str, GenerationInput]:
    return {ref: load_input(ref) for ref in ALL_REFS}


def test_every_cached_record_adapts(all_inputs) -> None:
    assert len(all_inputs) == 123
    for ref, inp in all_inputs.items():
        ids = [d.determination_id for d in inp.determinations]
        assert ids == ["risk_tier", "prohibited_practice", "actor_role:provider",
                       "actor_role:deployer", "obligation:aiact:art_16",
                       "obligation:aiact:art_26"], ref


def test_statuses_round_trip_to_the_record(all_inputs) -> None:
    """The adapter splits the compound status but never changes it."""
    for ref, inp in all_inputs.items():
        r = records()[ref]
        by_id = {d.determination_id: d for d in inp.determinations}
        assert by_id["risk_tier"].status_key == r["risk_tier"]
        assert by_id["prohibited_practice"].status_key == r["prohibited"]
        for group, status in r["obligations"].items():
            assert by_id[f"obligation:{group}"].status_key == status
        roles = r["actor_role"]
        for role in ("provider", "deployer"):
            d = by_id[f"actor_role:{role}"]
            if roles["status"] == "not_in_force":
                assert d.applicability_status == "not_in_force"
            elif role in roles["unresolved_roles"]:
                assert d.applicability_status == "unresolved"
            elif role in roles["actor_roles"]:
                assert d.applicability_status == "applicable"
            else:
                assert d.status_key == "not_applicable/conditions_not_met"


def test_contract_invariants(all_inputs) -> None:
    texts = {p.versioned_provision_id: p.text for p in corpus().provisions}
    for ref, inp in all_inputs.items():
        seen = set()
        for d in inp.determinations:
            assert d.applicability_status in STATUSES
            assert (d.not_applicable_reason is not None) == (d.applicability_status == "not_applicable")
            assert bool(d.unresolved_inputs) == (d.applicability_status == "unresolved"), (ref, d.determination_id)
            assert (d.applicable_from is not None) == (d.not_applicable_reason == "not_yet_applicable")
            assert (d.in_force_from is not None) == (d.applicability_status == "not_in_force")
            if d.applicability_status == "not_in_force":
                assert d.supporting_evidence == ()
            else:
                assert d.supporting_evidence, (ref, d.determination_id)
            for e in d.supporting_evidence:
                assert e.evidence_id not in seen
                seen.add(e.evidence_id)
                assert e.excerpt is None or e.excerpt == texts[e.versioned_provision_id]


def test_generator_input_carries_no_user_text(all_inputs) -> None:
    """Invariant 23: no description, no case_quote, no free-text facts."""
    from generation_fixtures import cases
    for ref, inp in all_inputs.items():
        r = records()[ref]
        case = cases()[r["case"]]
        blob = json.dumps(inp.as_prompt_data(), ensure_ascii=False)
        assert case["description"].strip()[:60] not in blob
        for v in (r["verdicts"] or {}).values():
            if v["case_quote"] and len(v["case_quote"]) > 25:
                assert v["case_quote"] not in blob, ref
        for name in ("intended_purpose", "ai_functionality", "role_in_decision_making",
                     "affected_persons"):
            value = case["gold_facts"].get(name)
            if value and len(value) > 15:
                assert value not in blob, (ref, name)
        assert "expected" not in blob and "gold" not in blob


def test_rationales_only_for_unanswered_verdicts(all_inputs) -> None:
    for ref, inp in all_inputs.items():
        verdicts = records()[ref]["verdicts"] or {}
        allowed = {v["rationale"] for v in verdicts.values() if v["verdict"] == "insufficient"}
        for d in inp.determinations:
            for m in d.unresolved_inputs:
                assert m.rationale is None or m.rationale in allowed
                if m.kind == "matcher_question":
                    assert verdicts[m.ref]["verdict"] == "insufficient"


def test_obligations_are_per_article_with_text_only_when_they_apply(all_inputs) -> None:
    for inp in all_inputs.values():
        for d in inp.determinations:
            if d.determination_type != "obligation_applicability":
                continue
            with_text = d.status_key in ("applicable", "not_applicable/not_yet_applicable")
            for e in d.supporting_evidence:
                assert (e.excerpt is not None) == with_text


def test_d32_2_is_reproduced_faithfully_even_though_gold_is_unresolved() -> None:
    """Invariant 26 / the known limitation: the adapter does not correct Phase 3."""
    inp = load_input("D32#2")
    assert inp.determination("risk_tier").status_key == "not_applicable/conditions_not_met"


def test_d10_unresolved_names_the_decisive_question() -> None:
    d = load_input("D10#0").determination("risk_tier")
    assert d.applicability_status == "unresolved"
    assert [m.ref for m in d.unresolved_inputs] == ["aiact:anx_III:area_4:item_b"]
    assert d.unresolved_inputs[0].question.startswith("Annex III(4)(b): ")
    assert "aiact:anx_III:area_4:item_b" in {e.logical_provision_id for e in d.supporting_evidence}


def test_d38_and_synthetic_cases_are_all_not_in_force() -> None:
    for inp in (load_input("D38#0"), load_synthetic("D01", "2024-07-31", "S01")):
        for d in inp.determinations:
            assert d.applicability_status == "not_in_force"
            assert d.in_force_from == date(2024, 8, 1)


def test_no_matcher_raises() -> None:
    with pytest.raises(AdapterError):
        NoMatcher().match("x", {})


def test_split_status() -> None:
    assert split_status("not_applicable/not_yet_applicable") == ("not_applicable", "not_yet_applicable")
    assert split_status("unresolved") == ("unresolved", None)

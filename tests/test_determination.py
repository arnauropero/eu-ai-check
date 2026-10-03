"""Phase 3 determination code, offline (no API calls).

The matcher is exercised with fake clients; the combination code, rule engine and
role rules with hand-built verdicts. The pre-registered prompt is SHA-pinned.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from eu_ai_check.corpus.models import Corpus
from eu_ai_check.determination.baseline import CutoffBaseline, youden_threshold
from eu_ai_check.determination.combine import prohibited_practice, risk_tier
from eu_ai_check.determination.evaluation import decide, load_cases, score_rule_path
from eu_ai_check.determination.logic import (
    APPLICABLE,
    NA_CONDITIONS,
    NA_NOT_YET,
    NOT_IN_FORCE,
    UNRESOLVED,
)
from eu_ai_check.determination.matcher import (
    DOES_NOT_MATCH,
    INSUFFICIENT,
    MATCHES,
    SYSTEM_PROMPT,
    OpenAIMatcher,
    Verdict,
    response_schema,
    verify,
)
from eu_ai_check.determination.pipeline import Pipeline
from eu_ai_check.determination.roles import determine_roles
from eu_ai_check.determination.scopes import SCOPES, candidates
from eu_ai_check.extraction.schema import CaseFacts

ROOT = Path(__file__).resolve().parents[1]
CORPUS = Corpus.load(ROOT / "data/corpus")
PIPELINE = Pipeline(CORPUS.provisions, CORPUS.applicability_rules)
ENGINE = PIPELINE.engine
TODAY = date(2026, 10, 3)
CANDS = candidates(CORPUS.provisions, TODAY)
ALL_IDS = [p.logical_provision_id for ps in CANDS.values() for p in ps]

#: SHA-256 of the matcher prompt, schema and scope questions as pre-registered.
#: Changing any of them after the first run is a new experiment, not a fix.
PRE_REGISTERED_MATCHER_SHA = "cf7df80994668d81128df7560bc792e3c653133940e8029a1346de3d0ab6ac85"


def matcher_digest() -> str:
    payload = json.dumps({
        "system": SYSTEM_PROMPT,
        "schema": response_schema(),
        "scopes": [(s.name, s.question) for s in SCOPES],
    }, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def test_matcher_prompt_is_pinned_as_pre_registered() -> None:
    assert matcher_digest() == PRE_REGISTERED_MATCHER_SHA


def verdicts(default: str = DOES_NOT_MATCH, **overrides: str) -> dict[str, Verdict]:
    """A verdict for every candidate; keyword args name ids with ':' as '__'."""
    out = {pid: Verdict(pid, default) for pid in ALL_IDS}
    for key, v in overrides.items():
        pid = "aiact:" + key.replace("__", ":")
        assert pid in out, pid
        out[pid] = Verdict(pid, v)
    return out


# --------------------------------------------------------------------------
# Candidates and scopes
# --------------------------------------------------------------------------

def test_candidate_sets_are_exhaustive_closed_lists() -> None:
    sizes = {name: len(ps) for name, ps in CANDS.items()}
    assert sizes == {"annex_iii": 25, "article_5": 8, "article_6_3_condition": 4,
                     "article_6_3_override": 1, "annex_i": 20, "article_6_1_b": 1}


def test_no_candidates_before_entry_into_force() -> None:
    assert not any(candidates(CORPUS.provisions, date(2024, 6, 15)).values())


# --------------------------------------------------------------------------
# Verification (§4.2): only ever downgrades
# --------------------------------------------------------------------------

DESC = "We use HireSmart, which scores CVs and shows recruiters a ‘ranked’ shortlist."
A4A = "aiact:anx_III:area_4:item_a"


def _raw(pid=A4A, verdict=MATCHES, case_quote="scores CVs",
         provision_quote="recruitment or selection of natural persons"):
    return {"provision_id": pid, "verdict": verdict, "case_quote": case_quote,
            "provision_quote": provision_quote, "rationale": "r"}


def test_verified_match_stands() -> None:
    out, log = verify([_raw()], CANDS, DESC)
    assert out[A4A].verdict == MATCHES and out[A4A].downgrade_reason is None


def test_missing_verdicts_become_insufficient() -> None:
    out, log = verify([_raw()], CANDS, DESC)
    other = "aiact:anx_III:area_4:item_b"
    assert out[other].verdict == INSUFFICIENT
    assert len(out) == len(ALL_IDS)


def test_unknown_id_is_discarded() -> None:
    out, log = verify([_raw(pid="aiact:art_99")], CANDS, DESC)
    assert "aiact:art_99" not in out
    assert any("unknown id" in line for line in log)


def test_non_verbatim_case_quote_is_downgraded() -> None:
    out, _ = verify([_raw(case_quote="ranks CVs")], CANDS, DESC)
    assert out[A4A].verdict == INSUFFICIENT
    assert out[A4A].model_verdict == MATCHES
    assert "case_quote" in out[A4A].downgrade_reason


def test_non_verbatim_provision_quote_is_downgraded() -> None:
    out, _ = verify([_raw(provision_quote="hiring people")], CANDS, DESC)
    assert out[A4A].verdict == INSUFFICIENT


def test_quote_normalisation_is_whitespace_and_quote_characters_only() -> None:
    out, _ = verify([_raw(case_quote="a  'ranked'\nshortlist")], CANDS, DESC)
    assert out[A4A].verdict == MATCHES
    out, _ = verify([_raw(case_quote="A 'RANKED' SHORTLIST")], CANDS, DESC)
    assert out[A4A].verdict == INSUFFICIENT, "no case folding"


def test_required_quotes_are_enforced() -> None:
    out, _ = verify([_raw(provision_quote=None)], CANDS, DESC)
    assert out[A4A].verdict == INSUFFICIENT
    out, _ = verify([_raw(verdict=DOES_NOT_MATCH, case_quote=None)], CANDS, DESC)
    assert out[A4A].verdict == INSUFFICIENT, "silence is not a negative"


def test_conflicting_duplicates_become_insufficient() -> None:
    out, _ = verify([_raw(), _raw(verdict=DOES_NOT_MATCH)], CANDS, DESC)
    assert out[A4A].verdict == INSUFFICIENT


def test_insufficient_is_never_upgraded() -> None:
    out, _ = verify([_raw(verdict=INSUFFICIENT)], CANDS, DESC)
    assert out[A4A].verdict == INSUFFICIENT


# --------------------------------------------------------------------------
# Risk-tier combination (§9.1)
# --------------------------------------------------------------------------

def test_annex_iii_match_without_derogation_is_high_risk() -> None:
    t = risk_tier(verdicts(anx_III__area_4__item_a=MATCHES), TODAY, ENGINE)
    assert (t.status, t.system_category, t.derogation) == (
        APPLICABLE, ["annex_iii_high_risk"], "not_established")


def test_derogation_applies_when_override_does_not_match() -> None:
    t = risk_tier(verdicts(anx_III__area_4__item_a=MATCHES,
                           art_6__para_3__point_a=MATCHES), TODAY, ENGINE)
    assert (t.status, t.derogation, t.system_category) == (NA_CONDITIONS, "applied", [])


def test_profiling_override_wins() -> None:
    t = risk_tier(verdicts(anx_III__area_4__item_a=MATCHES, art_6__para_3__point_d=MATCHES,
                           art_6__para_3__subpara_3=MATCHES), TODAY, ENGINE)
    assert (t.status, t.derogation) == (APPLICABLE, "overridden_by_profiling")


def test_unknown_override_with_a_condition_is_unresolved() -> None:
    t = risk_tier(verdicts(anx_III__area_3__item_a=MATCHES, art_6__para_3__point_d=MATCHES,
                           art_6__para_3__subpara_3=INSUFFICIENT), TODAY, ENGINE)
    assert t.status == UNRESOLVED


def test_silent_derogation_is_not_established_not_unresolved() -> None:
    v = verdicts(anx_III__area_4__item_a=MATCHES)
    for pid in v:
        if pid.startswith("aiact:art_6:para_3"):
            v[pid] = Verdict(pid, INSUFFICIENT)
    t = risk_tier(v, TODAY, ENGINE)
    assert (t.status, t.derogation) == (APPLICABLE, "not_established")


def test_annex_iii_route_before_its_date_is_not_yet_applicable() -> None:
    t = risk_tier(verdicts(anx_III__area_4__item_a=MATCHES), date(2026, 3, 1), ENGINE)
    assert (t.status, t.system_category) == (NA_NOT_YET, ["annex_iii_high_risk"])


def test_product_route_needs_both_conditions_and_its_own_date() -> None:
    both = verdicts(anx_I__item_11=MATCHES, art_6__para_1__point_b=MATCHES)
    assert risk_tier(both, TODAY, ENGINE).status == NA_NOT_YET
    assert risk_tier(both, date(2027, 9, 1), ENGINE).status == APPLICABLE
    unknown_b = verdicts(anx_I__item_1=MATCHES, art_6__para_1__point_b=INSUFFICIENT)
    assert risk_tier(unknown_b, TODAY, ENGINE).status == UNRESOLVED
    no_b = verdicts(anx_I__item_1=MATCHES, art_6__para_1__point_b=DOES_NOT_MATCH)
    assert risk_tier(no_b, TODAY, ENGINE).status == NA_CONDITIONS


def test_one_insufficient_annex_iii_item_leaves_the_tier_unresolved() -> None:
    t = risk_tier(verdicts(anx_III__area_3__item_d=INSUFFICIENT), TODAY, ENGINE)
    assert t.status == UNRESOLVED and t.high_risk is None


def test_no_verdicts_means_not_in_force() -> None:
    assert risk_tier({}, date(2024, 6, 15), ENGINE).status == NOT_IN_FORCE


# --------------------------------------------------------------------------
# Prohibited practice
# --------------------------------------------------------------------------

def test_prohibition_dates_from_february_2025() -> None:
    v = verdicts(art_5__para_1__point_f=MATCHES)
    assert prohibited_practice(v, TODAY, ENGINE).status == APPLICABLE
    assert prohibited_practice(v, date(2025, 1, 15), ENGINE).status == NA_NOT_YET
    assert prohibited_practice(verdicts(), TODAY, ENGINE).status == NA_CONDITIONS
    v = verdicts(art_5__para_1__point_f=INSUFFICIENT)
    assert prohibited_practice(v, TODAY, ENGINE).status == UNRESOLVED


# --------------------------------------------------------------------------
# Rule engine and roles
# --------------------------------------------------------------------------

def test_article_16_a_three_dates_three_states() -> None:
    """The §14.2 worked example on the real rules."""
    roles = {"provider": True, "deployer": False}
    routes = {"annex_iii_high_risk": True, "article_6_1_high_risk": False}
    pid = "aiact:art_16:point_a"
    assert ENGINE.evaluate(pid, date(2024, 7, 1), roles, routes).status == NOT_IN_FORCE
    assert ENGINE.evaluate(pid, date(2025, 6, 1), roles, routes).status == NA_NOT_YET
    assert ENGINE.evaluate(pid, date(2026, 9, 1), roles, routes).status == APPLICABLE


def test_a_failed_condition_beats_an_unknown_one() -> None:
    roles = {"provider": False, "deployer": True}
    routes = {"annex_iii_high_risk": None, "article_6_1_high_risk": None}
    assert ENGINE.evaluate("aiact:art_16:point_a", TODAY, roles, routes).status == NA_CONDITIONS
    assert ENGINE.evaluate("aiact:art_26:para_1", TODAY, roles, routes).status == UNRESOLVED


def test_provision_without_any_rule_is_a_coverage_gap() -> None:
    assert ENGINE.evaluate("aiact:art_6:para_5", TODAY, {}, {}).status == UNRESOLVED


def _facts(**kw) -> CaseFacts:
    base = dict(intended_purpose="x", ai_functionality="y", relevant_date=None,
                role_in_decision_making=None, develops_system=None, uses_system=None,
                puts_own_name_or_trademark=None, modifies_intended_purpose=None,
                modifies_safety_relevant_components=None, jurisdiction=None,
                affected_persons=None)
    base.update(kw)
    return CaseFacts(**base)


def test_rebranding_transfers_the_provider_role_only_on_high_risk_systems() -> None:
    f = _facts(develops_system=False, uses_system=False, puts_own_name_or_trademark=True,
               modifies_intended_purpose=False, modifies_safety_relevant_components=False)
    assert determine_roles(f, True).actor_roles == ["provider"]
    none = determine_roles(f, False)
    assert (none.actor_roles, none.status) == ([], "known")
    assert determine_roles(f, None).unresolved_roles == ["provider"]


def test_partial_role_knowledge_keeps_the_established_role() -> None:
    r = determine_roles(_facts(develops_system=None, uses_system=True), False)
    assert r.as_label() == {"actor_roles": ["deployer"], "unresolved_roles": ["provider"],
                            "status": "unresolved"}


def test_rule_path_reproduces_every_label() -> None:
    """Pre-registered target 1.00 for rule-based outputs on gold inputs."""
    result = score_rule_path(PIPELINE, load_cases(ROOT / "data/eval/determination_cases.json"))
    assert result["failures"] == [] and result["accuracy"] == 1.0


# --------------------------------------------------------------------------
# OpenAI matcher, with a fake client
# --------------------------------------------------------------------------

class FakeClient:
    def __init__(self, *, text=None, refusal=None, status="completed"):
        parts = []
        if refusal is not None:
            parts.append(SimpleNamespace(type="refusal", refusal=refusal))
        if text is not None:
            parts.append(SimpleNamespace(type="output_text", text=text))
        self.response = SimpleNamespace(
            output=[SimpleNamespace(type="message", content=parts)], status=status,
            model="gpt-6.1-sol-2026", usage=SimpleNamespace(input_tokens=10, output_tokens=5),
            incomplete_details=SimpleNamespace(reason="max_output_tokens"))
        self.requests = []
        self.responses = self

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return self.response


def test_request_shape_is_the_pre_registered_one() -> None:
    m = OpenAIMatcher(FakeClient(text="{}"), effort="low")
    req = m.request(DESC, CANDS)
    assert req["model"] == "gpt-6.1-sol"
    assert req["reasoning"] == {"effort": "low"}
    fmt = req["text"]["format"]
    assert fmt["strict"] is True and fmt["schema"] == response_schema()
    assert req["input"][0]["content"] == SYSTEM_PROMPT
    user = req["input"][1]["content"]
    assert DESC in user and all(pid in user for pid in ALL_IDS)


def test_only_pre_registered_efforts_are_accepted() -> None:
    with pytest.raises(ValueError):
        OpenAIMatcher(FakeClient(), effort="high")


def test_good_output_is_verified() -> None:
    client = FakeClient(text=json.dumps({"verdicts": [_raw()]}))
    result = OpenAIMatcher(client).match(DESC, CANDS)
    assert result.error is None and result.verdicts[A4A].verdict == MATCHES
    assert result.served_model == "gpt-6.1-sol-2026"


@pytest.mark.parametrize("client", [
    FakeClient(refusal="no"),
    FakeClient(text="{}", status="incomplete"),
    FakeClient(text="not json"),
], ids=["refusal", "incomplete", "unparseable"])
def test_failures_fail_closed(client) -> None:
    result = OpenAIMatcher(client).match(DESC, CANDS)
    assert result.error
    assert {v.verdict for v in result.verdicts.values()} == {INSUFFICIENT}
    assert len(result.verdicts) == len(ALL_IDS)


def test_pipeline_never_calls_the_matcher_before_entry_into_force() -> None:
    class Exploding:
        name = "x"

        def match(self, *a):
            raise AssertionError("matcher called with no candidates")

    d = PIPELINE.determine(_facts(uses_system=True), DESC, date(2024, 6, 15), Exploding())
    assert d.risk_tier.status == NOT_IN_FORCE and d.prohibited.status == NOT_IN_FORCE
    assert d.role_label()["status"] == NOT_IN_FORCE
    assert {d.group_status(g) for g in d.obligations} == {NOT_IN_FORCE}


# --------------------------------------------------------------------------
# Baseline and decision rule
# --------------------------------------------------------------------------

def test_youden_threshold_picks_the_best_cut_and_breaks_ties_high() -> None:
    assert youden_threshold([0.9, 0.8, 0.3, 0.2], [True, True, False, False]) == 0.8
    with pytest.raises(ValueError):
        youden_threshold([0.5], [True])


class FakeEncoder:
    """Deterministic vectors: the query matches passages sharing its first word."""

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(4)
        v[hash(text.split()[0]) % 4] = 1.0
        return v

    def encode_passages(self, texts):
        return np.stack([self._vec(t) for t in texts])

    def encode_query(self, text):
        return self._vec(text)


def test_baseline_marks_at_most_the_top_candidate_per_scope() -> None:
    tau = {s.name: 0.5 for s in SCOPES}
    result = CutoffBaseline(FakeEncoder(), tau).match("Annex text", CANDS)
    for scope, ps in CANDS.items():
        hits = [p for p in ps if result.verdicts[p.logical_provision_id].verdict == MATCHES]
        assert len(hits) <= 1, scope
    assert INSUFFICIENT not in {v.verdict for v in result.verdicts.values()}


def _score(macro_rt, macro_pp, per_state=0.9, zero=()):
    def det(macro):
        return {"per_state": {"applicable": per_state, "not_applicable": per_state,
                              "unresolved": per_state}, "macro": macro}
    return {"risk_tier": det(macro_rt), "prohibited_practice": det(macro_pp),
            "zero_tolerance_violations": list(zero), "not_in_force_deterministic": 1.0}


def test_decision_rule_requires_all_three_conditions() -> None:
    rules_ok = {"accuracy": 1.0}
    base = _score(0.6, 0.6, per_state=0.6)
    assert decide({"matcher-low": _score(0.9, 0.9), "baseline": base}, rules_ok)["adopt_matcher"]
    assert not decide({"matcher-low": _score(0.9, 0.65), "baseline": base},
                      rules_ok)["adopt_matcher"], "margin < 0.10 on one determination"
    assert not decide({"matcher-low": _score(0.9, 0.9, zero=["x"]), "baseline": base},
                      rules_ok)["adopt_matcher"]
    assert not decide({"matcher-low": _score(0.84, 0.84, per_state=0.84), "baseline": base},
                      rules_ok)["phase_3_acceptance_met"]

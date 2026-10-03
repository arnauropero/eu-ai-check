"""Verified verdicts -> risk tier and prohibited practice (SYSTEM_SPEC.md §4.2,
§9.1). Fixed code; the matcher only ever judges one candidate at a time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .logic import (
    APPLICABLE,
    NA_CONDITIONS,
    NA_NOT_YET,
    NOT_IN_FORCE,
    UNRESOLVED,
    Tri,
    combine_outcomes,
)
from .matcher import DOES_NOT_MATCH, INSUFFICIENT, MATCHES, Verdict
from .rules import RuleEngine
from .scopes import scope_of

ANNEX_III = "annex_iii_high_risk"
ARTICLE_6_1 = "article_6_1_high_risk"
ROUTE_PROVISION = {ANNEX_III: "aiact:art_6:para_2", ARTICLE_6_1: "aiact:art_6:para_1:point_a"}
OVERRIDE_ID = "aiact:art_6:para_3:subpara_3"
ARTICLE_6_1_B = "aiact:art_6:para_1:point_b"


def _by_scope(verdicts: dict[str, Verdict]) -> dict[str, list[Verdict]]:
    out: dict[str, list[Verdict]] = {}
    for v in verdicts.values():
        out.setdefault(scope_of(v.provision_id), []).append(v)
    return out


def any_of(verdicts: list[Verdict]) -> Tri:
    """A list scope: any match establishes it; all does_not_match rejects it;
    otherwise unknown. An empty scope is unknown, never a silent rejection."""
    if any(v.verdict == MATCHES for v in verdicts):
        return True
    if verdicts and all(v.verdict == DOES_NOT_MATCH for v in verdicts):
        return False
    return None


def single(verdict: Verdict | None) -> Tri:
    if verdict is None or verdict.verdict == INSUFFICIENT:
        return None
    return verdict.verdict == MATCHES


@dataclass
class RiskTier:
    status: str
    routes: dict[str, Tri] = field(default_factory=dict)  # established / not met / unknown
    derogation: str | None = None
    matched: list[str] = field(default_factory=list)

    @property
    def system_category(self) -> list[str]:
        return [c for c in (ANNEX_III, ARTICLE_6_1) if self.routes.get(c) is True]

    @property
    def high_risk(self) -> Tri:
        """For the role rules (§4.3): an established route counts, applicable yet
        or not."""
        if self.status == NOT_IN_FORCE:
            return None
        if any(v is True for v in self.routes.values()):
            return True
        return None if any(v is None for v in self.routes.values()) else False


def risk_tier(verdicts: dict[str, Verdict], on: date, engine: RuleEngine) -> RiskTier:
    if not verdicts:
        return RiskTier(NOT_IN_FORCE)
    scopes = _by_scope(verdicts)
    matched = [v.provision_id for v in verdicts.values() if v.verdict == MATCHES]

    # Article 6(2) route, with the 6(3) derogation and its profiling override.
    annex_iii = any_of(scopes.get("annex_iii", []))
    derogation = "not_assessed"
    if annex_iii is True:
        condition = any_of(scopes.get("article_6_3_condition", []))
        override = single(verdicts.get(OVERRIDE_ID))
        if condition is True and override is True:
            derogation = "overridden_by_profiling"
        elif condition is True and override is False:
            derogation, annex_iii = "applied", False
        elif condition is True:
            derogation, annex_iii = None, None  # the override would flip the outcome
        else:
            derogation = "not_established"
    elif annex_iii is None:
        derogation = None

    # Article 6(1) route: both conditions required.
    covered = any_of(scopes.get("annex_i", []))
    assessed = single(verdicts.get(ARTICLE_6_1_B))
    if covered is False or assessed is False:
        product = False
    elif covered is True and assessed is True:
        product = True
    else:
        product = None

    routes = {ANNEX_III: annex_iii, ARTICLE_6_1: product}
    outcomes = []
    for category, state in routes.items():
        if state is True:
            start = engine.route_start(ROUTE_PROVISION[category])
            outcomes.append(APPLICABLE if on >= start else NA_NOT_YET)
        else:
            outcomes.append(state)
    return RiskTier(combine_outcomes(outcomes), routes, derogation, matched)


@dataclass
class Prohibition:
    status: str
    points: list[str] = field(default_factory=list)


def prohibited_practice(verdicts: dict[str, Verdict], on: date,
                        engine: RuleEngine) -> Prohibition:
    article_5 = _by_scope(verdicts).get("article_5", [])
    if not article_5:
        return Prohibition(NOT_IN_FORCE)
    points = [v.provision_id for v in article_5 if v.verdict == MATCHES]
    state = any_of(article_5)
    if state is True:
        statuses = [engine.evaluate(pid, on, {}, {}).status for pid in points]
        if APPLICABLE in statuses:
            return Prohibition(APPLICABLE, points)
        if NA_NOT_YET in statuses:
            return Prohibition(NA_NOT_YET, points)
        return Prohibition(UNRESOLVED, points)
    return Prohibition(NA_CONDITIONS if state is False else UNRESOLVED, points)

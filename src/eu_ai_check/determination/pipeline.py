"""Case -> the four top-level determinations (SYSTEM_SPEC.md §2, §4).

Order matters: risk tier first, because the role rules read `high_risk` (§4.3),
and obligations read both roles and risk-tier routes (§12, §14.2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

from ..corpus.models import ApplicabilityRule, Provision
from ..extraction.schema import CaseFacts
from .combine import Prohibition, RiskTier, prohibited_practice, risk_tier
from .logic import NOT_IN_FORCE
from .matcher import MatchResult
from .roles import RoleResult, determine_roles
from .rules import ProvisionStatus, RuleEngine
from .scopes import candidates

OBLIGATION_GROUPS = ("aiact:art_16", "aiact:art_26")


class Matcher(Protocol):
    name: str

    def match(self, description: str,
              candidates: dict[str, list[Provision]]) -> MatchResult: ...


@dataclass
class Determination:
    risk_tier: RiskTier
    prohibited: Prohibition
    roles: RoleResult | None  # None before entry into force
    obligations: dict[str, list[ProvisionStatus]]
    match: MatchResult | None = None
    notes: list[str] = field(default_factory=list)

    def role_label(self) -> dict:
        if self.roles is None:
            return {"actor_roles": [], "unresolved_roles": [], "status": NOT_IN_FORCE}
        return self.roles.as_label()

    def group_status(self, group: str) -> str:
        """One status per obligation article; 'mixed' if its provisions disagree,
        which V1's uniform per-article rules should make impossible."""
        statuses = {s.status for s in self.obligations[group]}
        return statuses.pop() if len(statuses) == 1 else "mixed"


def downstream(facts: CaseFacts, on: date, tier: RiskTier,
               engine: RuleEngine) -> tuple[RoleResult | None, dict[str, list[ProvisionStatus]]]:
    """Roles and obligations from a risk tier. Pure rules: the evaluation feeds
    the *labelled* risk tier through here to score the rule code in isolation."""
    if tier.status == NOT_IN_FORCE:
        groups = {g: engine.evaluate_group(g, on, {}, {}) for g in OBLIGATION_GROUPS}
        return None, groups
    roles = determine_roles(facts, tier.high_risk)
    groups = {g: engine.evaluate_group(g, on, roles.values, tier.routes)
              for g in OBLIGATION_GROUPS}
    return roles, groups


class Pipeline:
    def __init__(self, provisions: list[Provision], rules: list[ApplicabilityRule]):
        self.provisions = provisions
        self.engine = RuleEngine(provisions, rules)

    def determine(self, facts: CaseFacts, description: str, on: date,
                  matcher: Matcher) -> Determination:
        cands = candidates(self.provisions, on)
        if not any(cands.values()):
            match = None
            verdicts = {}
        else:
            match = matcher.match(description, cands)
            verdicts = match.verdicts
        tier = risk_tier(verdicts, on, self.engine)
        prohibited = prohibited_practice(verdicts, on, self.engine)
        roles, obligations = downstream(facts, on, tier, self.engine)
        return Determination(tier, prohibited, roles, obligations, match)

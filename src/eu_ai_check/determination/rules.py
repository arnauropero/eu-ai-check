"""The `ApplicabilityRule` matching engine (SYSTEM_SPEC.md §12, §14, §14.2).

Conditions read determination outputs, not raw facts: `actor_role` reads the
three-valued role values (§4.3) and `system_category` reads the risk-tier route
states (§9.1). A rule's date window is evaluated like a condition; only a
provision with no rule at all is a coverage gap (`unresolved`).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from ..corpus.models import ApplicabilityRule, Provision
from .logic import (
    APPLICABLE,
    NA_NOT_YET,
    NOT_IN_FORCE,
    UNRESOLVED,
    Tri,
    combine_outcomes,
    k_and,
)

#: Route state per `system_category` value: True established, False not met,
#: None unresolved.
RouteStates = dict[str, Tri]


@dataclass(frozen=True)
class RuleOutcome:
    rule_id: str
    outcome: str | None | bool  # APPLICABLE | NA_NOT_YET | None | False


@dataclass(frozen=True)
class ProvisionStatus:
    logical_provision_id: str
    versioned_provision_id: str | None
    status: str
    rule_outcomes: tuple[RuleOutcome, ...] = ()


class RuleEngine:
    def __init__(self, provisions: list[Provision], rules: list[ApplicabilityRule]):
        self.provisions = {p.logical_provision_id: p for p in provisions}
        self.rules: dict[str, list[ApplicabilityRule]] = defaultdict(list)
        for rule in rules:
            self.rules[rule.legal_provision_id].append(rule)

    def route_start(self, logical_provision_id: str) -> date:
        """The `applicable_from` of a classification provision's rule: Article 6(2)
        for the Annex III route, Article 6(1) for the product route. Risk tier reads
        these dates only, never their conditions (§12)."""
        (rule,) = self.rules[logical_provision_id]
        return rule.applicable_from

    def evaluate(self, logical_provision_id: str, on: date,
                 roles: dict[str, Tri], routes: RouteStates) -> ProvisionStatus:
        provision = self.provisions[logical_provision_id]
        if not provision.text_valid_on(on):
            return ProvisionStatus(logical_provision_id, None, NOT_IN_FORCE)
        rules = self.rules.get(logical_provision_id, [])
        if not rules:
            return ProvisionStatus(logical_provision_id, provision.versioned_provision_id,
                                   UNRESOLVED)
        outcomes = []
        for rule in rules:
            role = rule.conditions["actor_role"]
            category = rule.conditions["system_category"]
            met = k_and(
                True if role == "any" else roles.get(role),
                True if category == "any" else routes.get(category, False),
            )
            if met is True:
                in_window = rule.applicable_from <= on and (
                    rule.applicable_to is None or on < rule.applicable_to)
                outcome = APPLICABLE if in_window else NA_NOT_YET
            else:
                outcome = met
            outcomes.append(RuleOutcome(rule.rule_id, outcome))
        return ProvisionStatus(
            logical_provision_id,
            provision.versioned_provision_id,
            combine_outcomes([o.outcome for o in outcomes]),
            tuple(outcomes),
        )

    def evaluate_group(self, prefix: str, on: date, roles: dict[str, Tri],
                       routes: RouteStates) -> list[ProvisionStatus]:
        """Every provision under an article-level id such as `aiact:art_16`."""
        return [
            self.evaluate(pid, on, roles, routes)
            for pid in self.provisions
            if pid.startswith(prefix + ":")
        ]

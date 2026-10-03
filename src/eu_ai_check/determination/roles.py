"""Actor-role rules (SYSTEM_SPEC.md §4.1, §4.3). No LLM.

    deployer  = uses_system                                   Article 3(4)
    substantial_modification = modifies_safety_relevant_components
                               OR modifies_intended_purpose   Article 3(23)
    provider  = develops_system                               Article 3(3)
                OR (high_risk AND (puts_own_name_or_trademark  Article 25(1)(a)
                                   OR substantial_modification Article 25(1)(b)
                                   OR modifies_intended_purpose)) Article 25(1)(c)

All three-valued. `high_risk` is true when a risk-tier route is established
(whether or not it applies yet), unknown when risk tier is unresolved.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..extraction.schema import CaseFacts
from .logic import Tri, k_and, k_or

ROLES = ("provider", "deployer")


@dataclass(frozen=True)
class RoleResult:
    values: dict[str, Tri]
    substantial_modification: Tri
    evidence: dict[str, list[str]] = field(default_factory=dict)

    @property
    def actor_roles(self) -> list[str]:
        return [r for r in ROLES if self.values[r] is True]

    @property
    def unresolved_roles(self) -> list[str]:
        return [r for r in ROLES if self.values[r] is None]

    @property
    def status(self) -> str:
        return "unresolved" if self.unresolved_roles else "known"

    def as_label(self) -> dict:
        return {"actor_roles": self.actor_roles,
                "unresolved_roles": self.unresolved_roles, "status": self.status}


def determine_roles(facts: CaseFacts, high_risk: Tri) -> RoleResult:
    substantial = k_or(facts.modifies_safety_relevant_components,
                       facts.modifies_intended_purpose)
    transfer = k_and(high_risk, k_or(facts.puts_own_name_or_trademark, substantial,
                                     facts.modifies_intended_purpose))
    provider = k_or(facts.develops_system, transfer)
    evidence = {
        "deployer": ["aiact:art_3:point_4"],
        "provider": ["aiact:art_3:point_3"],
    }
    if transfer is not False:
        if facts.puts_own_name_or_trademark is not False:
            evidence["provider"].append("aiact:art_25:para_1:point_a")
        if substantial is not False:
            evidence["provider"] += ["aiact:art_25:para_1:point_b", "aiact:art_3:point_23"]
        if facts.modifies_intended_purpose is not False:
            evidence["provider"].append("aiact:art_25:para_1:point_c")
    return RoleResult(
        values={"provider": provider, "deployer": facts.uses_system},
        substantial_modification=substantial,
        evidence=evidence,
    )

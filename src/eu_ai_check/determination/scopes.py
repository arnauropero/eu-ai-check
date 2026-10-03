"""Matcher scopes: the closed candidate sets and the question asked of each
(SYSTEM_SPEC.md §4.2, §9.1).

Candidates are every date-eligible provision in the scope -- exhaustive, never
top-k -- so a negative determination never rests on a retrieval miss.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable

from ..corpus.models import Provision


@dataclass(frozen=True)
class Scope:
    name: str
    question: str
    contains: Callable[[str], bool]


SCOPES: tuple[Scope, ...] = (
    Scope(
        "annex_iii",
        "Is the AI system intended to be used as described in this Annex III item?",
        lambda pid: pid.startswith("aiact:anx_III:"),
    ),
    Scope(
        "article_5",
        "Does the described practice fall under this prohibition, with none of the "
        "exceptions stated in the provision applying?",
        lambda pid: pid.startswith("aiact:art_5:para_1:point_"),
    ),
    Scope(
        "article_6_3_condition",
        "Does this Article 6(3) condition hold for the AI system as described?",
        lambda pid: pid.startswith("aiact:art_6:para_3:point_"),
    ),
    Scope(
        "article_6_3_override",
        "Does the AI system perform profiling of natural persons (automated "
        "evaluation of personal aspects such as performance at work, economic "
        "situation, health, preferences, reliability or behaviour)?",
        lambda pid: pid == "aiact:art_6:para_3:subpara_3",
    ),
    Scope(
        "annex_i",
        "Is the AI system intended to be used as a safety component of a product, or "
        "is it itself a product, covered by this Union harmonisation legislation?",
        lambda pid: pid.startswith("aiact:anx_I:"),
    ),
    Scope(
        "article_6_1_b",
        "Is the product (or the AI system itself as a product) required to undergo a "
        "third-party conformity assessment under that Union harmonisation legislation?",
        lambda pid: pid == "aiact:art_6:para_1:point_b",
    ),
)
SCOPE_BY_NAME = {s.name: s for s in SCOPES}


def candidates(provisions: list[Provision], on: date) -> dict[str, list[Provision]]:
    """Date-eligible candidates per scope. An empty result for every scope means
    no text was in force on `on` (§14.1 `not_in_force`)."""
    return {
        scope.name: [
            p for p in provisions
            if scope.contains(p.logical_provision_id) and p.text_valid_on(on)
        ]
        for scope in SCOPES
    }


def scope_of(logical_provision_id: str) -> str:
    for scope in SCOPES:
        if scope.contains(logical_provision_id):
            return scope.name
    raise KeyError(logical_provision_id)

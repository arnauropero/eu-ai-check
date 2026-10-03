"""Three-valued logic and the four-state status vocabulary (SYSTEM_SPEC.md §14.2).

`None` is *unknown*. Unknown is never read as false: an unknown input can only be
outweighed by a definite false in a conjunction or a definite true in a
disjunction.
"""

from __future__ import annotations

from typing import Optional

Tri = Optional[bool]

APPLICABLE = "applicable"
UNRESOLVED = "unresolved"
NOT_IN_FORCE = "not_in_force"
NA_CONDITIONS = "not_applicable/conditions_not_met"
NA_NOT_YET = "not_applicable/not_yet_applicable"
STATUSES = (APPLICABLE, UNRESOLVED, NOT_IN_FORCE, NA_CONDITIONS, NA_NOT_YET)


def k_or(*values: Tri) -> Tri:
    if any(v is True for v in values):
        return True
    return False if all(v is False for v in values) else None


def k_and(*values: Tri) -> Tri:
    if any(v is False for v in values):
        return False
    return True if all(v is True for v in values) else None


def combine_outcomes(outcomes: list[str | None | bool]) -> str:
    """Fold per-rule (or per-route) outcomes into one status (§14.2).

    Each outcome is `APPLICABLE`, `NA_NOT_YET` (conditions met, date not yet
    reached), `None` (unknown) or `False` (conditions not met). One met rule
    suffices; otherwise unknown wins over a definite exclusion; and
    not_yet_applicable wins over conditions_not_met as the less reassuring reason.
    """
    if APPLICABLE in outcomes:
        return APPLICABLE
    if any(o is None for o in outcomes):
        return UNRESOLVED
    if NA_NOT_YET in outcomes:
        return NA_NOT_YET
    return NA_CONDITIONS

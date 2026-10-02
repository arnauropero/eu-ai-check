"""Zero-tolerance conclusion-leakage check (SYSTEM_SPEC.md §3.1, §18).

Two ways extraction can leak a legal conclusion, checked separately:

1. **A conclusion field.** Structurally prevented (schema + `extra="forbid"`), and
   still checked on the raw model output, so a schema regression is caught by a
   named failure rather than by luck.
2. **A conclusion inside a free-text value.** Structured outputs cannot prevent
   `intended_purpose = "high-risk recruitment system under Annex III"`. This is
   the realistic failure, especially when the user's own description asserts the
   conclusion ("we are the provider of a high-risk system").

The term list below was fixed **before the extractor was first run** (Phase 2
pre-registration). It targets the Act's conclusion vocabulary -- risk tiers, the
two V1 actor roles, substantial modification, and article/annex citations -- and
deliberately not ordinary words that merely overlap ("healthcare provider" is a
fact; "the provider of this AI system" is a role conclusion). Any later change to
the list must be recorded as post-hoc.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .schema import EXCLUDED_FIELDS, TEXT_FIELDS

#: (name, pattern). Case-insensitive.
CONCLUSION_TERMS: tuple[tuple[str, str], ...] = (
    ("risk tier: high-risk", r"\bhigh[\s-]risk\b"),
    ("risk tier: other tiers", r"\b(?:unacceptable|limited|minimal)[\s-]risk\b"),
    ("risk tier: named", r"\brisk\s+(?:tier|level|category|classification)\b"),
    ("prohibited practice", r"\bprohibited\s+(?:ai\s+)?practices?\b"),
    ("actor role: deployer", r"\bdeployers?\b"),
    ("actor role: provider", r"\b(?:ai|system)\s+providers?\b|\bproviders?\s+of\s+(?:the|this|a|an|its|our)\b"
                             r"|\bas\s+(?:the\s+|a\s+)?providers?\b|\bdownstream\s+providers?\b"),
    ("substantial modification", r"\bsubstantial(?:ly)?\s+modif"),
    ("legal citation: article", r"\barticles?\s+\d+"),
    ("legal citation: annex", r"\bannex\s+[ivx]+\b"),
)
_COMPILED = [(name, re.compile(p, re.IGNORECASE)) for name, p in CONCLUSION_TERMS]

#: Free-text fields whose values are scanned. `jurisdiction` is included: "EU
#: high-risk regime" is no more acceptable there than anywhere else.
SCANNED_FIELDS = (*TEXT_FIELDS, "jurisdiction")


@dataclass(frozen=True)
class Leak:
    field: str
    kind: str      # "excluded_field" or a CONCLUSION_TERMS name
    excerpt: str


def find_leaks(raw_output: dict[str, Any]) -> list[Leak]:
    """Scan the model's raw JSON output -- before pydantic validation, so an
    excluded field is reported as a leak rather than only as a parse error."""
    leaks = [
        Leak(field, "excluded_field", repr(raw_output[field])[:80])
        for field in sorted(EXCLUDED_FIELDS & raw_output.keys())
    ]
    for field in SCANNED_FIELDS:
        value = raw_output.get(field)
        if not isinstance(value, str):
            continue
        for name, pattern in _COMPILED:
            m = pattern.search(value)
            if m:
                lo, hi = max(0, m.start() - 30), min(len(value), m.end() + 30)
                leaks.append(Leak(field, name, value[lo:hi]))
    return leaks

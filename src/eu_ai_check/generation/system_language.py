"""System-language rate: REPORTED ONLY, never a gate (dev pass 1 review, 2026-10-06).

Counts claims that describe how the analysis was produced ("The code recorded ...",
"The supplied evidence ... no excerpts") instead of the law and the user's system.
It does not feed C0-C8, the adoption rule or any pass rate. The lexicon is pinned
by SHA in a test. Legal uses of the same words are not matched: the "assessment"
pattern only takes system-side phrasings, so "conformity assessment" or "data
protection impact assessment" never count; "supplied" only counts in "supplied
evidence", so Article 26(9)'s "supplied information" does not. A lexical count can
still miss paraphrases or catch rare legal uses of "records".
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from typing import Any, Iterable

SYSTEM_LANGUAGE_LEXICON: dict[str, str] = {
    "code": r"\bcode(?:['’]s)?\b",
    "explanation": r"\bexplanation\b",
    "records/recorded": r"\b(?:records|recorded)\b",
    "match/matched/matching": r"\b(?:match|matched|matching)\b",
    "supplied evidence": r"\bsupplied evidence\b",
    "excerpt(s)": r"\bexcerpts?\b",
    "the cited": r"\bthe cited\b",
    "by citation": r"\bby citation\b",
    "system-side assessment": (
        r"\b(?:the assessment checked|(?:its|the code['’]s|code['’]s|practice|obligation"
        r"|separate) assessment|the assessment of these obligations|matters to assessing)\b"),
}
_PATTERNS = {k: re.compile(v, re.IGNORECASE) for k, v in SYSTEM_LANGUAGE_LEXICON.items()}


def lexicon_sha() -> str:
    return hashlib.sha256(json.dumps(SYSTEM_LANGUAGE_LEXICON, sort_keys=True)
                          .encode("utf-8")).hexdigest()


def phrases(text: str) -> list[tuple[str, str]]:
    """(lexicon entry, exact matched phrase) for every hit in one claim text."""
    return [(k, m.group(0)) for k, p in _PATTERNS.items() for m in p.finditer(text)]


def system_language(claims: Iterable[tuple[str, str, str]]) -> dict[str, Any]:
    """`claims` = (record ref, claim_id, text). Claims affected / total, and
    occurrences per lexicon entry."""
    total, affected = 0, []
    per: Counter = Counter()
    for ref, cid, text in claims:
        total += 1
        hits = phrases(text)
        for k, _ in hits:
            per[k] += 1
        if hits:
            affected.append({"record": ref, "claim_id": cid,
                             "phrases": [p for _, p in hits]})
    return {
        "lexicon_sha256": lexicon_sha(),
        "claims_affected": len(affected),
        "claims_total": total,
        "rate": round(len(affected) / total, 4) if total else None,
        "occurrences": {k: per.get(k, 0) for k in SYSTEM_LANGUAGE_LEXICON},
        "affected": affected,
    }


def claims_of(rows: Iterable[dict]) -> list[tuple[str, str, str]]:
    """Every claim of every parsed raw answer in a runs file (fallbacks included:
    the metric reads what the model wrote, not what shipped)."""
    out = []
    for r in rows:
        if not r.get("raw"):
            continue
        try:
            claims = json.loads(r["raw"])["claims"]
        except (ValueError, KeyError, TypeError):
            continue
        out += [(r["ref"], c.get("claim_id"), c.get("text", "")) for c in claims]
    return out

"""Fake OpenAI Responses client for the Phase 5 runner tests. No network, no key.

It answers each stage from the case labels:
- extraction (`case_facts`): the case's gold facts, unless a test overrides them;
- matcher (`provision_verdicts`): an oracle that reproduces the labelled risk tier
  and prohibited practice (`unresolved` -> `insufficient` for that scope);
- generation (`generated_answer`): an empty claim list (template-equivalent text).
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace
from typing import Any, Callable

from eu_ai_check.determination.scopes import scope_of

OVERRIDE = "aiact:art_6:para_3:subpara_3"


def response(text: str, input_tokens: int = 1000, output_tokens: int = 500, rid: str = "r") -> Any:
    return SimpleNamespace(
        id=rid, created_at=1761350400, model="gpt-6.1-sol", status="completed",
        incomplete_details=None,
        output=[SimpleNamespace(type="message",
                                content=[SimpleNamespace(type="output_text", text=text)])],
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens,
                              output_tokens_details=None))


def _description(user: str, start: str, end: str) -> str:
    return user.split(start, 1)[1].split(end, 1)[0].strip()


def _candidates(user: str) -> dict[str, str]:
    out, pid = {}, None
    for line in user.splitlines():
        if line.startswith("- provision_id: "):
            pid = line.removeprefix("- provision_id: ").strip()
        elif pid and line.startswith("  text: "):
            out[pid] = line.removeprefix("  text: ")
            pid = None
    return out


def oracle_verdicts(case: dict, candidates: dict[str, str]) -> list[dict]:
    """Verdicts that make the pipeline produce the case's labelled risk tier and
    prohibited practice."""
    e = case["expected"]
    rt, pp = e["risk_tier"], e["prohibited_practice"]
    desc = case["description"]
    quote = desc[:20]
    out = []
    condition_done = False
    for pid, text in candidates.items():
        scope = scope_of(pid)
        verdict = "does_not_match"
        if scope == "annex_iii":
            if rt["status"] == "unresolved" and not rt["annex_iii"]:
                verdict = "insufficient"
            elif pid in rt["annex_iii"]:
                verdict = "matches"
        elif scope == "annex_i":
            if pid in rt["annex_i"]:
                verdict = "matches"
        elif scope == "article_6_1_b":
            if rt["annex_i"]:
                verdict = "matches"
        elif scope == "article_6_3_condition":
            if rt["derogation"] in ("applied", "overridden_by_profiling") and not condition_done:
                verdict, condition_done = "matches", True
        elif scope == "article_6_3_override":
            if rt["derogation"] == "overridden_by_profiling":
                verdict = "matches"
        elif scope == "article_5":
            if pp["status"] == "unresolved":
                verdict = "insufficient"
            elif pid in pp["points"]:
                verdict = "matches"
        item = {"provision_id": pid, "rationale": "fake", "verdict": verdict,
                "case_quote": None if verdict == "insufficient" else quote,
                "provision_quote": text[:20] if verdict == "matches" else None}
        out.append(item)
    return out


class FakeOpenAI:
    def __init__(self, cases: list[dict], *,
                 facts: Callable[[dict], dict] | None = None,
                 verdicts: Callable[[dict, dict[str, str]], list[dict]] | None = None,
                 raise_on: Callable[[str, dict, int], Exception | None] | None = None,
                 tokens: dict[str, tuple[int, int]] | None = None):
        self.by_desc = {c["description"].strip(): c for c in cases}
        self.facts = facts or (lambda c: c["gold_facts"])
        self.verdicts = verdicts or oracle_verdicts
        self.raise_on = raise_on
        self.tokens = tokens or {}
        self.responses = self
        self.calls: list[tuple[str, str]] = []

    def create(self, **request: Any) -> Any:
        stage = request["text"]["format"]["name"]
        user = request["input"][1]["content"]
        if stage == "case_facts":
            case = self.by_desc[_description(user, "<description>", "</description>")]
        elif stage == "provision_verdicts":
            case = self.by_desc[_description(user, "USE CASE DESCRIPTION:", "CANDIDATE PROVISIONS:")]
        else:
            case = None
        ref = case["id"] if case else "gen"
        n = sum(1 for s, r in self.calls if (s, r) == (stage, ref))
        self.calls.append((stage, ref))
        if self.raise_on and (exc := self.raise_on(stage, case, n)) is not None:
            raise exc
        tin, tout = self.tokens.get(stage, (1000, 500))
        if stage == "case_facts":
            return response(json.dumps(self.facts(case)), tin, tout, f"x-{ref}")
        if stage == "provision_verdicts":
            return response(json.dumps({"verdicts": self.verdicts(case, _candidates(user))}),
                            tin, tout, f"m-{ref}")
        return response(json.dumps({"claims": []}), tin, tout, "g")


def provision_ids(user: str) -> list[str]:
    return re.findall(r"^- provision_id: (\S+)$", user, re.M)

"""The provision matcher: the one LLM step between extraction and generation
(SYSTEM_SPEC.md §2, §4.2).

One Responses API call per case covers every scope. The model returns one
verdict per candidate (`matches` / `does_not_match` / `insufficient`) with
verbatim quotes. Code then verifies each verdict; a failed check *downgrades* it
to `insufficient` and is logged. Nothing ever upgrades a verdict. A refusal, an
incomplete response, or output that does not parse makes every candidate
`insufficient` (fail closed).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from ..corpus.models import Provision
from .scopes import SCOPES

MATCHES = "matches"
DOES_NOT_MATCH = "does_not_match"
INSUFFICIENT = "insufficient"
VERDICTS = (MATCHES, DOES_NOT_MATCH, INSUFFICIENT)

OPENAI_MODEL = "gpt-6.1-sol"
#: Pre-registered effort arms (SYSTEM_SPEC.md §18): M-low is primary.
EFFORT_MAP = {"low": "low", "medium": "medium"}
SCHEMA_NAME = "provision_verdicts"

SYSTEM_PROMPT = """\
You assess whether an AI use case falls under specific provisions of the EU AI Act
(Regulation (EU) 2024/1689). You are given the user's own description of the use case
and a closed list of candidate provisions, grouped by scope. Each scope states the
question to answer for each of its candidates.

Return exactly one verdict for every candidate, identified by its provision_id:

- "matches": the description shows the question is answered yes for this candidate.
  Give case_quote (an exact span copied from the description that shows it) and
  provision_quote (an exact span copied from the candidate's text that it meets).
- "does_not_match": the description shows the question is answered no. Give
  case_quote: an exact span from the description showing the use case is something
  else. provision_quote may be null.
- "insufficient": the description does not say enough to answer either way. Both
  quotes may be null.

Rules:
- Silence is not a negative. If the description does not address the question, the
  verdict is "insufficient", never "does_not_match".
- But a description of what the system is and does can answer a question "no". A
  system described as filtering email spam is evidently not a medical device, a
  machinery safety component or a recruitment tool; the span describing what it does
  is the case_quote. Use "insufficient" only when the answer depends on something
  the description leaves open.
- Judge only from the description. Do not assume facts that typical systems of this
  kind would have.
- Quotes must be copied character for character from the description or the
  candidate text. Do not paraphrase, abbreviate, or join separate passages.
- Judge each candidate independently. Several candidates may match.
- rationale is one or two sentences explaining the verdict.
"""

#: POST-HOC prompt variant (SYSTEM_SPEC.md §18, opened 2026-10-04), appended to the
#: frozen SYSTEM_PROMPT as one more rule. It targets the Article 5(1)(c) over-caution
#: seen on the dev set and is validated only on the held-out set
#: data/eval/determination_heldout_5_1_c.json. It deliberately names no use-case
#: domain, so the held-out cases are not taught by example.
SOCIAL_SCORING_V1 = """\
- Article 5(1)(c) (social scoring) requires a score of people, built from their
  social behaviour or personal characteristics, that leads to detrimental treatment
  in a social context unrelated to where the data came from, or to treatment that is
  unjustified or disproportionate. An evaluation of people that the description
  shows is used for its own stated purpose, in the same context its data comes
  from, is lawful purpose-specific evaluation, not social scoring: answer
  "does_not_match" and quote the span stating that purpose. The description does not
  have to rule out every element of the provision. Answer "insufficient" only when
  the description shows people being scored or classified on their behaviour or
  characteristics and leaves open how the score is used, by whom, or what treatment
  follows.
"""

#: POST-HOC variant v2 (SYSTEM_SPEC.md §18, 2026-10-04). v1's rule sat in the system
#: prompt and spilled over to other provisions on the dev set (D32 Annex III 1(c),
#: D24 Article 5(1)(e)). v2 changes nothing in the system prompt: the guidance is
#: printed only inside the Article 5(1)(c) candidate's own block of the user message,
#: so it is never in view as an instruction for any other candidate.
SOCIAL_SCORING_V2_ID = "aiact:art_5:para_1:point_c"
SOCIAL_SCORING_V2 = (
    "Guidance for this candidate only, Article 5(1)(c) social scoring; it does not "
    "apply to any other candidate. An evaluation of people that the description shows "
    "is used for its own stated purpose, with data from that same context, and with no "
    "stated use of the score in an unrelated social context and no stated unjustified "
    "or disproportionate treatment, is purpose-specific evaluation, not social scoring "
    "under this point: answer \"does_not_match\" and quote the span stating that purpose. "
    "For this point, answer \"insufficient\" only when the description shows people "
    "being scored or classified on their social behaviour or personal characteristics "
    "and leaves open how the score is used, by whom, or what treatment follows."
)

#: Prompt variants. "baseline" is the pre-registered prompt (SHA-pinned in
#: tests/test_determination.py) and the default everywhere. Each variant is
#: (system-prompt suffix, {provision_id: note printed in that candidate's block}).
PROMPT_VARIANTS: dict[str, tuple[str, dict[str, str]]] = {
    "baseline": ("", {}),
    "social-scoring-v1": (SOCIAL_SCORING_V1, {}),
    "social-scoring-v2": ("", {SOCIAL_SCORING_V2_ID: SOCIAL_SCORING_V2}),
}
DEFAULT_PROMPT_VARIANT = "baseline"


def system_prompt(variant: str = DEFAULT_PROMPT_VARIANT) -> str:
    if variant not in PROMPT_VARIANTS:
        raise ValueError(f"unknown prompt variant {variant!r} ({sorted(PROMPT_VARIANTS)})")
    return SYSTEM_PROMPT + PROMPT_VARIANTS[variant][0]


def provision_notes(variant: str = DEFAULT_PROMPT_VARIANT) -> dict[str, str]:
    system_prompt(variant)  # validates the variant name
    return PROMPT_VARIANTS[variant][1]


def response_schema() -> dict[str, Any]:
    nullable = {"type": ["string", "null"]}
    return {
        "type": "object",
        "properties": {
            "verdicts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "provision_id": {"type": "string"},
                        "rationale": {"type": "string"},
                        "verdict": {"type": "string", "enum": list(VERDICTS)},
                        "case_quote": nullable,
                        "provision_quote": nullable,
                    },
                    "required": ["provision_id", "rationale", "verdict", "case_quote",
                                 "provision_quote"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["verdicts"],
        "additionalProperties": False,
    }


def user_message(description: str, candidates: dict[str, list[Provision]],
                 notes: dict[str, str] | None = None) -> str:
    parts = ["USE CASE DESCRIPTION:", description.strip(), "", "CANDIDATE PROVISIONS:"]
    for scope in SCOPES:
        provisions = candidates.get(scope.name) or []
        if not provisions:
            continue
        parts += ["", f"## Scope: {scope.name}", f"Question: {scope.question}"]
        for p in provisions:
            parts += [f"- provision_id: {p.logical_provision_id}",
                      f"  citation: {p.citation()}", f"  text: {p.text}"]
            if notes and p.logical_provision_id in notes:
                parts.append(f"  note: {notes[p.logical_provision_id]}")
    return "\n".join(parts)


# --------------------------------------------------------------------------
# Verification (§4.2): code, not the model, decides whether a verdict stands
# --------------------------------------------------------------------------

_QUOTE_CHARS = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"'})


def _norm(text: str) -> str:
    """Whitespace and quote-character normalisation only -- no case folding, no
    punctuation stripping: a quote must really be in the text."""
    return re.sub(r"\s+", " ", text.translate(_QUOTE_CHARS)).strip()


@dataclass(frozen=True)
class Verdict:
    provision_id: str
    verdict: str
    case_quote: str | None = None
    provision_quote: str | None = None
    rationale: str = ""
    model_verdict: str | None = None  # what the model said, before verification
    downgrade_reason: str | None = None


def verify(raw: list[dict[str, Any]], candidates: dict[str, list[Provision]],
           description: str) -> tuple[dict[str, Verdict], list[str]]:
    """Apply checks 1-4 of §4.2. Returns one verified verdict per candidate and
    the log of downgrades and discards."""
    texts = {p.logical_provision_id: p.text for ps in candidates.values() for p in ps}
    log: list[str] = []
    seen: dict[str, list[dict[str, Any]]] = {}
    for item in raw:
        pid = item.get("provision_id")
        if pid not in texts:  # check 1
            log.append(f"discarded verdict for unknown id {pid!r}")
            continue
        seen.setdefault(pid, []).append(item)

    case_norm = _norm(description)
    out: dict[str, Verdict] = {}
    for pid, text in texts.items():
        items = seen.get(pid)
        if not items:  # check 2: missing
            log.append(f"{pid}: no verdict -> insufficient")
            out[pid] = Verdict(pid, INSUFFICIENT, downgrade_reason="missing verdict")
            continue
        if len({i.get("verdict") for i in items}) > 1:  # check 2: conflicting duplicates
            log.append(f"{pid}: conflicting duplicate verdicts -> insufficient")
            out[pid] = Verdict(pid, INSUFFICIENT, model_verdict="conflicting",
                               downgrade_reason="conflicting duplicates")
            continue
        item = items[0]
        verdict = item.get("verdict")
        case_quote = item.get("case_quote") or None
        provision_quote = item.get("provision_quote") or None
        reason = None
        if verdict not in VERDICTS:
            reason = f"unknown verdict {verdict!r}"
        elif verdict in (MATCHES, DOES_NOT_MATCH) and not case_quote:  # check 4
            reason = "case_quote required"
        elif verdict == MATCHES and not provision_quote:  # check 4
            reason = "provision_quote required"
        elif case_quote and _norm(case_quote) not in case_norm:  # check 3
            reason = "case_quote not verbatim in description"
        elif provision_quote and _norm(provision_quote) not in _norm(text):  # check 3
            reason = "provision_quote not verbatim in provision text"
        if reason and verdict != INSUFFICIENT:
            log.append(f"{pid}: {verdict} -> insufficient ({reason})")
            out[pid] = Verdict(pid, INSUFFICIENT, case_quote, provision_quote,
                               item.get("rationale", ""), verdict, reason)
        else:
            out[pid] = Verdict(pid, verdict if verdict in VERDICTS else INSUFFICIENT,
                               case_quote, provision_quote, item.get("rationale", ""),
                               verdict)
    return out, log


# --------------------------------------------------------------------------
# The OpenAI call
# --------------------------------------------------------------------------

@dataclass
class MatchResult:
    verdicts: dict[str, Verdict]
    log: list[str] = field(default_factory=list)
    error: str | None = None
    served_model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    raw: str | None = None


def all_insufficient(candidates: dict[str, list[Provision]], reason: str) -> dict[str, Verdict]:
    return {
        p.logical_provision_id: Verdict(p.logical_provision_id, INSUFFICIENT,
                                        downgrade_reason=reason)
        for ps in candidates.values() for p in ps
    }


class OpenAIMatcher:
    def __init__(self, client: Any = None, *, model: str = OPENAI_MODEL, effort: str = "low",
                 prompt_variant: str = DEFAULT_PROMPT_VARIANT):
        system_prompt(prompt_variant)  # validates the variant name
        if effort not in EFFORT_MAP:
            raise ValueError(f"effort {effort!r} is not a pre-registered arm ({sorted(EFFORT_MAP)})")
        if client is None:
            import openai

            client = openai.OpenAI()
        self.client = client
        self.model = model
        self.effort = effort
        self.prompt_variant = prompt_variant
        self.name = f"matcher-{effort}" + (
            "" if prompt_variant == DEFAULT_PROMPT_VARIANT else f"+{prompt_variant}")

    def request(self, description: str, candidates: dict[str, list[Provision]]) -> dict[str, Any]:
        return {
            "model": self.model,
            "input": [
                {"role": "system", "content": system_prompt(self.prompt_variant)},
                {"role": "user", "content": user_message(
                    description, candidates, provision_notes(self.prompt_variant))},
            ],
            "reasoning": {"effort": EFFORT_MAP[self.effort]},
            "max_output_tokens": 32000,
            "text": {"format": {"type": "json_schema", "name": SCHEMA_NAME,
                                "schema": response_schema(), "strict": True}},
        }

    def match(self, description: str, candidates: dict[str, list[Provision]]) -> MatchResult:
        from ..extraction.openai_extractor import _message_content

        response = self.client.responses.create(**self.request(description, candidates))
        usage = getattr(response, "usage", None)
        result = MatchResult(
            verdicts={},
            served_model=getattr(response, "model", None),
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
        )
        refusal, text = _message_content(response)
        status = getattr(response, "status", None)
        if refusal is not None:
            result.error = f"refusal: {refusal[:120]}"
        elif status == "incomplete":
            reason = getattr(getattr(response, "incomplete_details", None), "reason", None)
            result.error = f"incomplete response ({reason})"
        elif status not in (None, "completed"):
            result.error = f"response status {status}"
        else:
            result.raw = text
            try:
                raw = json.loads(text or "")["verdicts"]
            except (ValueError, KeyError, TypeError) as exc:
                result.error = f"unparseable output ({type(exc).__name__})"
            else:
                result.verdicts, result.log = verify(raw, candidates, description)
                return result
        result.verdicts = all_insufficient(candidates, result.error)
        return result

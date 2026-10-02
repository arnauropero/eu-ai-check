"""Schema-constrained fact extraction (SYSTEM_SPEC.md §3) -- shared core and the
Anthropic provider.

The extraction model for Phase 2 is **OpenAI GPT-6.1 Sol** (`openai_extractor.py`,
the default in `providers.py`). This module holds what every provider shares --
the system prompt, the FIELD RULES, the result type and the fail-closed output
handling -- plus the original Claude Opus 5.5 implementation, kept as an
alternative provider option. Both providers send the same prompt and enforce the
same `CaseFacts` schema, so swapping them changes the model under test and nothing
in the pre-registered evaluation.

Claude call: `output_config.format` = the `CaseFacts` JSON schema; `fallbacks:
"default"` (beta `server-side-fallback-2026-07-01`) re-runs a safety decline on a
fallback model, and whichever model answered is recorded; explicit `effort`.

Fail-closed, for every provider: a refusal, a truncated response or output that
does not validate yields `facts=None` with the reason -- never a partially filled
`CaseFacts`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from pydantic import ValidationError

from .leakage import Leak, find_leaks
from .schema import CaseFacts, output_schema

ANTHROPIC_MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
DEFAULT_EFFORT = "medium"

SYSTEM_PROMPT = """\
You extract facts from a user's description of an AI system, for a later EU AI Act \
assessment. Another stage of the pipeline makes every legal determination, after \
retrieving the relevant provisions. Your output is only the factual input to that stage.

Record what the description states or reasonably implies, in plain factual language. \
Do not record legal conclusions or legal classifications -- for example whether the \
system is high-risk or prohibited, which actor role the organisation holds (such as \
provider or deployer), whether a change counts as a substantial modification, or which \
article or annex applies. If the description asserts such a conclusion, leave the \
conclusion out and record only the underlying facts it rests on: what the system does, \
who built it, who uses it, what was changed.

When the description does not state or reasonably imply a fact, use null. A null is a \
correct answer; a guess is not. The actor fields describe the organisation writing the \
description ("we", "our company"), or the organisation the description is about if it \
is written in the third person.

Write every value in English, whatever the language of the description.

FIELD RULES
{rules}"""

#: The labelling rules, shared verbatim with the eval set's guidelines so the model
#: is held to the same definitions the labels were written against.
FIELD_RULES = """\
- develops_system: true if the organisation built the system or had it built for \
itself; false if it obtained an existing system from someone else (bought, licensed, \
a built-in feature of another product). A separate legal entity in the same group, \
such as a sister company, counts as someone else. Adapting or fine-tuning a system \
obtained from someone else is recorded under the modifies_* fields, not here. null \
if the origin is not stated.
- uses_system: true if the organisation operates the system in its own activities, \
now or as planned; false if it only builds, sells or supplies it for others to use. \
null if not stated.
- puts_own_name_or_trademark: true if the organisation offers or markets the system \
under its own name or brand; false if the system is offered or used under another \
party's name (including a named third-party product used without rebranding). null \
if branding cannot be determined.
- modifies_intended_purpose: true if the organisation uses the system for a different \
purpose than the one it was supplied for; false only if the description says it is \
used as supplied or unchanged. null otherwise, including for systems the organisation \
built itself.
- modifies_safety_relevant_components: true if the organisation retrained, fine-tuned \
or changed the model or its components after obtaining it; false only if the \
description says it is used as supplied or unchanged. null otherwise.
- relevant_date: if the description describes a future launch or start of use with a \
date, use that date; if it asks for the situation at a specific date, use that date; \
if it says the assessment is about the present ("as of today"), use the assessment \
date. Resolve relative expressions against the assessment date; a month-level \
expression ("next month") means the first day of that month. If the system is \
already in use and no assessment date is asked for, or the timing is vague \
("sometime next year"), use null.
- role_in_decision_making and affected_persons: null unless the description says how \
the output is used or whom it concerns. Do not fill them in from what systems of this \
kind usually do.
- jurisdiction: the country or region where the system is used, if stated."""

#: Phase 2 follow-up experiment (pre-registered 2026-10-02, SYSTEM_SPEC.md §18):
#: one clarification appended AFTER the field rules, so FIELD_RULES -- shared
#: verbatim with the frozen eval set -- stays byte-identical. It restates L8's
#: condition for role_in_decision_making; it does not change the definition.
ROLE_NULL_V1 = """\
CLARIFICATION: role_in_decision_making
- Fill role_in_decision_making only when the description says what happens with the \
system's output: who acts on it or decides with it, or that the system itself acts or \
decides without a person. A description of the system's task alone (for example "it \
translates text" or "it transcribes audio") does not say how its output is used.
- If the description does not say how the output is used, return null. Do not write a \
value that only says the role, the decision-maker or human involvement is not \
described."""

#: Prompt variants. "baseline" is the frozen prompt of the first run (82467bb) and
#: the default everywhere; other variants exist only for follow-up experiments.
PROMPT_VARIANTS = {
    "baseline": "",
    "role-null-v1": "\n\n" + ROLE_NULL_V1,
}
DEFAULT_PROMPT_VARIANT = "baseline"


@dataclass
class ExtractionResult:
    facts: CaseFacts | None
    raw: dict[str, Any] | None
    leaks: list[Leak] = field(default_factory=list)
    #: What was asked for, recorded on every result (Phase 2 requirement).
    provider: str | None = None
    requested_model: str | None = None
    effort: str | None = None
    prompt_variant: str | None = None
    #: What actually answered, as reported by the API -- may be a dated snapshot
    #: or, on Anthropic, a fallback model.
    served_model: str | None = None
    fell_back: bool = False
    stop_reason: str | None = None
    error: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def ok(self) -> bool:
        return self.facts is not None


def system_prompt(variant: str = DEFAULT_PROMPT_VARIANT) -> str:
    if variant not in PROMPT_VARIANTS:
        raise ValueError(f"unknown prompt variant {variant!r} ({sorted(PROMPT_VARIANTS)})")
    return SYSTEM_PROMPT.format(rules=FIELD_RULES) + PROMPT_VARIANTS[variant]


def user_message(description: str, assessment_date: date) -> str:
    return (
        f"Assessment date: {assessment_date.isoformat()}\n\n"
        f"<description>\n{description}\n</description>"
    )


def finish(result: ExtractionResult, text: str | None) -> ExtractionResult:
    """Shared, provider-independent output handling: parse JSON, scan for
    conclusion leaks, validate against `CaseFacts`. The pydantic validation is
    the final enforcement of the schema for every provider, whatever the
    provider-side constraint was."""
    if text is None:
        result.error = "no text output in response"
        return result
    try:
        result.raw = json.loads(text)
    except json.JSONDecodeError as e:
        result.error = f"invalid JSON: {e}"
        return result
    if not isinstance(result.raw, dict):
        result.error = "JSON output is not an object"
        return result
    # Scan before validating: an excluded field must show up as a leak, not
    # only as a validation error.
    result.leaks = find_leaks(result.raw)
    try:
        result.facts = CaseFacts.model_validate(result.raw)
    except ValidationError as e:
        result.error = f"schema validation failed: {e.error_count()} error(s)"
    return result


class AnthropicExtractor:
    """Claude Opus 5.5 extraction -- a provider option, not the Phase 2 default."""

    provider = "anthropic"

    def __init__(self, client: Any = None, *, model: str = ANTHROPIC_MODEL,
                 effort: str = DEFAULT_EFFORT, use_fallbacks: bool = True):
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self.client = client
        self.model = model
        self.effort = effort
        self.use_fallbacks = use_fallbacks
        self._schema = output_schema()

    def request(self, description: str, assessment_date: date) -> dict[str, Any]:
        """The request body, separated out so tests can assert its shape."""
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 16000,
            "system": system_prompt(),
            "messages": [{"role": "user", "content": user_message(description, assessment_date)}],
            "output_config": {
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": self._schema},
            },
        }
        if self.use_fallbacks:
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        return kwargs

    def extract(self, description: str, assessment_date: date) -> ExtractionResult:
        response = self.client.beta.messages.create(**self.request(description, assessment_date))
        result = ExtractionResult(
            facts=None,
            raw=None,
            provider=self.provider,
            requested_model=self.model,
            effort=self.effort,
            served_model=getattr(response, "model", None),
            stop_reason=response.stop_reason,
            fell_back=_fell_back(response),
            input_tokens=getattr(response.usage, "input_tokens", 0) or 0,
            output_tokens=getattr(response.usage, "output_tokens", 0) or 0,
        )
        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            result.error = f"refusal ({getattr(details, 'category', None)})"
            return result
        if response.stop_reason == "max_tokens":
            result.error = "truncated at max_tokens"
            return result
        text = next((b.text for b in response.content if b.type == "text"), None)
        return finish(result, text)


def _fell_back(response: Any) -> bool:
    """True when a fallback model served (part of) this turn."""
    iterations = getattr(response.usage, "iterations", None) or []
    return any(getattr(entry, "type", None) == "fallback_message" for entry in iterations)

"""Schema-constrained fact extraction with Claude (SYSTEM_SPEC.md §3).

One Messages API call per description:

  * `output_config.format` = the `CaseFacts` JSON schema, so decoding is
    constrained to exactly the allowed fields;
  * `fallbacks: "default"` (beta `server-side-fallback-2026-07-01`), so a safety
    decline is re-run server-side on Anthropic's recommended fallback model
    instead of failing. Whichever model actually answered is recorded on every
    result, so a fallback can never silently mix models into eval numbers;
  * adaptive thinking (always on for Opus 5.5) with an explicit `effort`, since
    Opus 5.5's default (`medium`) differs from earlier Opus models.

Fail-closed: a refusal, a truncated response or output that does not validate
yields `facts=None` with the reason -- never a partially filled `CaseFacts`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from pydantic import ValidationError

from .leakage import Leak, find_leaks
from .schema import CaseFacts, output_schema

MODEL = "claude-opus-5-5"
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


@dataclass
class ExtractionResult:
    facts: CaseFacts | None
    raw: dict[str, Any] | None
    leaks: list[Leak] = field(default_factory=list)
    served_model: str | None = None
    fell_back: bool = False
    stop_reason: str | None = None
    error: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def ok(self) -> bool:
        return self.facts is not None


def user_message(description: str, assessment_date: date) -> str:
    return (
        f"Assessment date: {assessment_date.isoformat()}\n\n"
        f"<description>\n{description}\n</description>"
    )


class Extractor:
    def __init__(self, client: Any = None, *, model: str = MODEL, effort: str = DEFAULT_EFFORT,
                 use_fallbacks: bool = True):
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
            "system": SYSTEM_PROMPT.format(rules=FIELD_RULES),
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
        if text is None:
            result.error = "no text block in response"
            return result
        try:
            result.raw = json.loads(text)
        except json.JSONDecodeError as e:
            result.error = f"invalid JSON: {e}"
            return result

        # Scan before validating: an excluded field must show up as a leak, not
        # only as a validation error.
        result.leaks = find_leaks(result.raw)
        try:
            result.facts = CaseFacts.model_validate(result.raw)
        except ValidationError as e:
            result.error = f"schema validation failed: {e.error_count()} error(s)"
        return result


def _fell_back(response: Any) -> bool:
    """True when a fallback model served (part of) this turn."""
    iterations = getattr(response.usage, "iterations", None) or []
    return any(getattr(entry, "type", None) == "fallback_message" for entry in iterations)

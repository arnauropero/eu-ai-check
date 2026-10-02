"""Phase 2 extraction model: OpenAI GPT-6.1 Sol (SYSTEM_SPEC.md §3).

One Responses API call per description:

  * model `gpt-6.1-sol`;
  * `reasoning.effort` set explicitly per eval arm. GPT-6.1 Sol supports `low`,
    `medium` (its default), `high`, `xhigh` and `max` -- not `none` or `minimal` --
    so the pre-registered `low` and `medium` arms map onto it exactly, with no
    approximation;
  * Structured Outputs with `strict: true` over `openai_strict_schema()`, the
    `CaseFacts` schema in OpenAI's documented strict form;
  * the same system prompt and FIELD RULES as every other provider
    (`extractor.system_prompt()`), sent as a system message.

The requested model and effort, and the model the API reports as having served
the request, are recorded on every result.

Fail-closed, identically to the Anthropic path: a refusal content item, an
incomplete response (`max_output_tokens`, content filter), or output that does
not validate against `CaseFacts` yields `facts=None` with the reason. There is no
server-side fallback here: a refusal is reported as a refusal.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from .extractor import ExtractionResult, finish, system_prompt, user_message
from .schema import openai_strict_schema

OPENAI_MODEL = "gpt-6.1-sol"
#: Pre-registered effort arm -> GPT-6.1 Sol `reasoning.effort`. Identity mapping:
#: both values are natively supported (OpenAI model page, checked 2026-10-02).
EFFORT_MAP = {"low": "low", "medium": "medium"}
SCHEMA_NAME = "case_facts"


class OpenAIExtractor:
    provider = "openai"

    def __init__(self, client: Any = None, *, model: str = OPENAI_MODEL, effort: str = "medium"):
        if effort not in EFFORT_MAP:
            raise ValueError(f"effort {effort!r} is not a pre-registered arm ({sorted(EFFORT_MAP)})")
        if client is None:
            import openai

            client = openai.OpenAI()
        self.client = client
        self.model = model
        self.effort = effort
        self._schema = openai_strict_schema()

    def request(self, description: str, assessment_date: date) -> dict[str, Any]:
        """The request body, separated out so tests can assert its shape."""
        return {
            "model": self.model,
            "input": [
                {"role": "system", "content": system_prompt()},
                {"role": "user", "content": user_message(description, assessment_date)},
            ],
            "reasoning": {"effort": EFFORT_MAP[self.effort]},
            "max_output_tokens": 16000,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": SCHEMA_NAME,
                    "schema": self._schema,
                    "strict": True,
                }
            },
        }

    def extract(self, description: str, assessment_date: date) -> ExtractionResult:
        response = self.client.responses.create(**self.request(description, assessment_date))
        usage = getattr(response, "usage", None)
        result = ExtractionResult(
            facts=None,
            raw=None,
            provider=self.provider,
            requested_model=self.model,
            effort=EFFORT_MAP[self.effort],
            served_model=getattr(response, "model", None),
            stop_reason=getattr(response, "status", None),
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
        )

        refusal, text = _message_content(response)
        if refusal is not None:
            result.error = f"refusal: {refusal[:120]}"
            return result
        if getattr(response, "status", None) == "incomplete":
            reason = getattr(getattr(response, "incomplete_details", None), "reason", None)
            result.error = f"incomplete response ({reason})"
            return result
        if getattr(response, "status", None) not in (None, "completed"):
            result.error = f"response status {response.status}"
            return result
        return finish(result, text)


def _message_content(response: Any) -> tuple[str | None, str | None]:
    """(refusal text, output text) from the Responses API output items. A
    refusal is a separate content item, not an error status, so it has to be
    looked for explicitly."""
    refusal = text = None
    for item in getattr(response, "output", None) or []:
        if getattr(item, "type", None) != "message":
            continue
        for part in getattr(item, "content", None) or []:
            kind = getattr(part, "type", None)
            if kind == "refusal" and refusal is None:
                refusal = part.refusal
            elif kind == "output_text" and text is None:
                text = part.text
    return refusal, text

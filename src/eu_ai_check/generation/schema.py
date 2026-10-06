"""The claims-first output schema (SYSTEM_SPEC.md §16.3).

There is deliberately no status field: a model cannot set or change a status
(invariant 24). `response_schema()` is the strict JSON schema sent to the API;
`GeneratedAnswer` is what check C0 parses against.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_NAME = "generated_answer"


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1)
    determination_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    evidence_ids: list[str]
    quote: str | None
    quote_evidence_id: str | None


class GeneratedAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claims: list[Claim]


def parse_answer(text: str | None) -> tuple[GeneratedAnswer | None, str | None]:
    """(answer, None) on success, (None, reason) otherwise. This is check C0's
    parse step: strict, with no repair and no partial acceptance."""
    if not text:
        return None, "empty output"
    try:
        return GeneratedAnswer.model_validate_json(text), None
    except ValueError as exc:
        return None, f"schema violation: {str(exc).splitlines()[0][:160]}"


def response_schema() -> dict[str, Any]:
    nullable = {"type": ["string", "null"]}
    return {
        "type": "object",
        "properties": {
            "claims": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "claim_id": {"type": "string"},
                        "determination_id": {"type": "string"},
                        "text": {"type": "string"},
                        "evidence_ids": {"type": "array", "items": {"type": "string"}},
                        "quote": nullable,
                        "quote_evidence_id": nullable,
                    },
                    "required": ["claim_id", "determination_id", "text", "evidence_ids",
                                 "quote", "quote_evidence_id"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["claims"],
        "additionalProperties": False,
    }

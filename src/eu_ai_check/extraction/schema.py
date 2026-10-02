"""The extraction schema and the fact / conclusion boundary (SYSTEM_SPEC.md §3).

`CaseFacts` is exactly what the extractor may output. Two properties make the
boundary structural rather than a matter of prompt discipline:

  * `extra="forbid"` here, and `additionalProperties: false` in the JSON schema
    sent to the API, so a conclusion-level *field* (`risk_tier`, `actor_roles`,
    ...) cannot be emitted at all -- structured outputs constrain decoding to the
    schema, and validation rejects anything else.
  * Every field is nullable except the two retrieval-core descriptors. A fact the
    description does not state or reasonably imply is `null`, never a guess.
    Forcing a value would make the extractor invent facts, which is a quieter
    version of the same failure as inventing conclusions.

What structured outputs *cannot* stop is a conclusion smuggled into a free-text
value ("a high-risk recruitment system under Annex III"). That is checked
separately, in `leakage.py`.

**Deviations from SYSTEM_SPEC.md §3.2, decided in Phase 2:**

  * `relevant_date` is nullable. Many descriptions state no date; the extractor
    reports `null` and the pipeline substitutes the assessment date explicitly
    (`Case.relevant_date_basis` records which happened). The spec typed it as a
    required `date`, which would force the extractor to decide a default -- a
    pipeline policy, not a fact in the text.
  * `role_in_decision_making` is nullable for the same reason.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

#: Conclusion-level fields that extraction must never output (SYSTEM_SPEC.md §3.2,
#: §18 zero-tolerance metric). Listed explicitly so the check is named, not implied.
EXCLUDED_FIELDS: frozenset[str] = frozenset({
    "risk_tier",
    "prohibited_practice",
    "actor_roles",
    "actor_role",
    "applicable_articles",
    "obligations",
    "substantial_modification",
})


class CaseFacts(BaseModel):
    """Facts stated or reasonably implied by the user's description -- nothing
    that requires interpreting a term the AI Act defines."""

    model_config = ConfigDict(extra="forbid")

    # Retrieval-core
    intended_purpose: str = Field(
        description="The domain or purpose the AI system is used for, in plain "
        "words (e.g. 'screening job applicants for an employer')."
    )
    ai_functionality: str = Field(
        description="What the AI system technically does (e.g. 'ranks CVs "
        "against a job description')."
    )
    relevant_date: date | None = Field(
        description="The date the scenario should be assessed at, when the "
        "description gives one (see FIELD RULES). null otherwise."
    )

    # Retrieval / classification support
    role_in_decision_making: str | None = Field(
        description="How the system's output is used: whether it informs a "
        "human decision or acts automatically, and who decides (e.g. 'shortlists "
        "candidates; a recruiter makes the final call'). null if not described."
    )

    # Actor facts -- raw facts feeding role determination downstream
    develops_system: bool | None = Field(
        description="Does the describing organisation develop / build the AI "
        "system (in-house, or have it built to its own specification)? null if "
        "not stated or implied."
    )
    uses_system: bool | None = Field(
        description="Does the describing organisation use / operate the AI "
        "system itself? null if not stated or implied."
    )
    puts_own_name_or_trademark: bool | None = Field(
        description="Does the organisation market or present the system under "
        "its own name, brand or trademark? null if not stated or implied."
    )
    modifies_intended_purpose: bool | None = Field(
        description="Has the organisation changed what the system is used for "
        "compared with how it was originally supplied? null if not stated."
    )
    modifies_safety_relevant_components: bool | None = Field(
        description="Has the organisation changed the system's model, weights, "
        "training, or safety-relevant components after receiving it? null if "
        "not stated."
    )

    # Context
    jurisdiction: str | None = Field(
        description="Country or region where the system is used, if stated. "
        "null otherwise."
    )

    # Grounding / explanation support
    affected_persons: str | None = Field(
        description="Who the system's outputs are about or affect (e.g. 'job "
        "applicants'). null if not described."
    )


#: Field groups, used by the evaluator to pick a scoring method per field.
TEXT_FIELDS = ("intended_purpose", "ai_functionality", "role_in_decision_making", "affected_persons")
BOOL_FIELDS = (
    "develops_system",
    "uses_system",
    "puts_own_name_or_trademark",
    "modifies_intended_purpose",
    "modifies_safety_relevant_components",
)
DATE_FIELDS = ("relevant_date",)
SHORT_TEXT_FIELDS = ("jurisdiction",)
ALL_FIELDS = tuple(CaseFacts.model_fields)


def output_schema() -> dict[str, Any]:
    """JSON schema for `output_config.format`, derived from `CaseFacts`.

    Structured outputs need every property listed in `required` and
    `additionalProperties: false`; nullability is carried by the property's own
    type (`anyOf: [..., {type: null}]`), not by leaving it out of `required`.
    """
    schema = CaseFacts.model_json_schema()
    schema.pop("title", None)
    for prop in schema["properties"].values():
        prop.pop("title", None)
        prop.pop("default", None)
    schema["required"] = list(schema["properties"])
    schema["additionalProperties"] = False
    return schema


def openai_strict_schema() -> dict[str, Any]:
    """The same `CaseFacts` schema, in the form OpenAI Structured Outputs
    (`strict: true`) documents.

    Derived mechanically from `output_schema()`, so both providers constrain the
    same fields, types and nullability. Two transformations, both deliberate:

      * nullable fields are written as a type array (`{"type": ["string",
        "null"]}`), the nullable form OpenAI's documentation shows, instead of
        `anyOf`;
      * `"format": "date"` on `relevant_date` is dropped, because OpenAI's
        documentation did not confirm strict-mode support for string formats.
        The date constraint is not lost: every output is validated by pydantic
        (`CaseFacts.relevant_date: date | None`), and a value that is not an ISO
        date fails closed as a schema-validation error. The field description
        states the expected format.

    Everything else -- property set, `required`, `additionalProperties: false`,
    descriptions -- is unchanged, and tests compare the two schemas.
    """
    base = output_schema()
    props: dict[str, Any] = {}
    for name, prop in base["properties"].items():
        if "anyOf" in prop:
            types = [alt["type"] for alt in prop["anyOf"]]
            new = {"type": [t for t in types if t != "null"] + ["null"]}
        else:
            new = {"type": prop["type"]}
        description = prop.get("description", "")
        if name in DATE_FIELDS:
            description = f"{description} Format: YYYY-MM-DD.".strip()
        if description:
            new["description"] = description
        props[name] = new
    return {
        "type": "object",
        "properties": props,
        "required": list(props),
        "additionalProperties": False,
    }


class DateBasis(StrEnum):
    STATED = "stated"            # the description gave or implied the date
    ASSESSMENT_DATE = "assessment_date"  # none given; the pipeline defaulted it


class Case(BaseModel):
    """`CaseFacts` plus the pipeline's explicit date policy and the raw text.

    The free text is kept alongside the structured fields (SYSTEM_SPEC.md §3.3):
    structured fields enrich retrieval, they do not replace the description.
    """

    model_config = ConfigDict(extra="forbid")

    facts: CaseFacts
    description: str
    relevant_date: date
    relevant_date_basis: DateBasis

    @classmethod
    def from_facts(cls, facts: CaseFacts, description: str, assessment_date: date) -> Case:
        if facts.relevant_date is not None:
            return cls(facts=facts, description=description,
                       relevant_date=facts.relevant_date, relevant_date_basis=DateBasis.STATED)
        return cls(facts=facts, description=description,
                   relevant_date=assessment_date, relevant_date_basis=DateBasis.ASSESSMENT_DATE)

"""Canonical corpus data model.

Implements SYSTEM_SPEC.md §5 (Provision), §6 (variable-depth StructuralPath),
§10 (three-way identity model), §11 (text versioning) and §12 (ApplicabilityRule).

The three identities on `Provision` are deliberately separate and must not be
collapsed (§10):

    logical_provision_id    stable legal identity, survives amendment
    versioned_provision_id  one specific text version of that legal provision
    content_id              SHA-256 of normalized text, for embedding dedup
"""

from __future__ import annotations

import json
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SegmentType(StrEnum):
    """Levels observed in the real corpus (SYSTEM_SPEC.md §6, §9).

    Depth is variable, not fixed: Article 16 is Article -> Point (2 levels),
    Article 5 reaches Article -> Paragraph -> Point -> Sub-point (4 levels).

    `SUBPARAGRAPH` holds a paragraph's trailing subparagraph that follows its
    enumerated list and names no point of it. Article 6(3), third subparagraph
    (the profiling override) is the verified case. `SECTION` also appears inside
    an annex: Annex I is split into Sections A and B.
    """

    CHAPTER = "chapter"
    SECTION = "section"
    ARTICLE = "article"
    PARAGRAPH = "paragraph"
    SUBPARAGRAPH = "subparagraph"
    POINT = "point"
    SUB_POINT = "sub_point"
    ANNEX = "annex"
    ANNEX_AREA = "annex_area"
    ANNEX_ITEM = "annex_item"


#: Short form of each level, used to build `logical_provision_id`s. Shared so that
#: id construction (ingest) and id resolution (validation) can never drift apart --
#: they did drift once, on `annex` -> "anx" vs "ann".
SEGMENT_ABBREV: dict[SegmentType, str] = {
    SegmentType.CHAPTER: "ch",
    SegmentType.SECTION: "sec",
    SegmentType.ARTICLE: "art",
    SegmentType.PARAGRAPH: "para",
    SegmentType.SUBPARAGRAPH: "subpara",
    SegmentType.POINT: "point",
    SegmentType.SUB_POINT: "sub",
    SegmentType.ANNEX: "anx",
    SegmentType.ANNEX_AREA: "area",
    SegmentType.ANNEX_ITEM: "item",
}


_ORDINALS = ("first", "second", "third", "fourth", "fifth", "sixth", "seventh",
             "eighth", "ninth", "tenth")


def _ordinal(value: str) -> str:
    """'3' -> 'third', the Act's own way of citing a subparagraph."""
    return _ORDINALS[int(value) - 1]


class ProvisionType(StrEnum):
    """Legal character of a provision.

    Drives which Phase 0 invariants apply: `obligation` and `prohibition`
    provisions must carry applicability rules or a declared gap (§12.1).
    """

    DEFINITION = "definition"
    OBLIGATION = "obligation"
    PROHIBITION = "prohibition"
    EXCEPTION = "exception"
    SCOPE = "scope"
    ANNEX_CATEGORY = "annex_category"


class StructuralSegment(BaseModel):
    """One step in a provision's structural path."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: SegmentType
    value: str = Field(min_length=1)

    def __str__(self) -> str:
        return f"{self.type.value}={self.value}"


# An ordered path of variable length -- NOT a fixed set of named fields (§6).
StructuralPath = Annotated[list[StructuralSegment], Field(min_length=1)]


class ApplicabilityRule(BaseModel):
    """When, and under what conditions, a legal rule actually binds (§12).

    Deliberately separate from `Provision.text_valid_from/to`: text validity and
    applicability are two independent temporal questions (§13.1). `applicable_from`
    is recorded verbatim from source material (Article 113), never inferred from
    text validity.

    `legal_provision_id` references a `logical_provision_id`, not a versioned id --
    applicability is a property of the legal rule across its whole lifetime,
    independent of textual redrafting.
    """

    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(min_length=1)
    legal_provision_id: str = Field(min_length=1)
    applicable_from: date
    applicable_to: date | None = None
    conditions: dict[str, Any]
    scope: dict[str, Any]
    source_reference: str = Field(min_length=1)

    @model_validator(mode="after")
    def _temporal_fields_ordered(self) -> ApplicabilityRule:
        """§12.1: `applicable_to`, if present, is strictly after `applicable_from`."""
        if self.applicable_to is not None and self.applicable_to <= self.applicable_from:
            raise ValueError(
                f"rule {self.rule_id}: applicable_to ({self.applicable_to}) must be "
                f"strictly after applicable_from ({self.applicable_from})"
            )
        return self

    def in_force_on(self, on: date) -> bool:
        """Whether this rule binds on `on`. Half-open interval: [from, to)."""
        if on < self.applicable_from:
            return False
        return self.applicable_to is None or on < self.applicable_to


class Provision(BaseModel):
    """The canonical citable unit: the smallest independently interpretable legal
    unit that can be retrieved and cited without losing its interpreting
    conditions (§5).

    `text` holds the provision's complete canonical text, including any nested
    sub-structure. Article 5(1)(h) is the verified case: its three exceptions and
    closing savings clause stay inside the one canonical provision, so an
    embedding match on an exception can never be surfaced without the prohibition
    it excepts from (§7.1).
    """

    model_config = ConfigDict(extra="forbid")

    logical_provision_id: str = Field(min_length=1)
    versioned_provision_id: str = Field(min_length=1)
    content_id: str = Field(pattern=r"^[0-9a-f]{64}$")

    provision_type: ProvisionType
    structural_location: StructuralPath

    text: str = Field(min_length=1)
    heading: str | None = None

    text_valid_from: date
    text_valid_to: date | None = None

    cross_references: list[str] = Field(default_factory=list)

    # Set during ingestion when an obligation/prohibition provision is knowingly
    # left without an ApplicabilityRule, so §12.1's no-orphan check can tell an
    # intentional gap apart from an ingestion miss.
    applicability_gap_reason: str | None = None

    @model_validator(mode="after")
    def _text_validity_ordered(self) -> Provision:
        """§11: a version's own validity range must be ordered."""
        if self.text_valid_to is not None and self.text_valid_to <= self.text_valid_from:
            raise ValueError(
                f"{self.versioned_provision_id}: text_valid_to ({self.text_valid_to}) "
                f"must be strictly after text_valid_from ({self.text_valid_from})"
            )
        return self

    @property
    def requires_applicability_rule(self) -> bool:
        """§12.1: obligations and prohibitions are the rule-carrying types."""
        return self.provision_type in (
            ProvisionType.OBLIGATION,
            ProvisionType.PROHIBITION,
        )

    @property
    def depth(self) -> int:
        return len(self.structural_location)

    def citation(self) -> str:
        """Render a human citation by walking the path (§6) -- never by reading
        fixed columns, because the path depth varies by provision."""
        parts: list[str] = []
        in_annex = False
        for seg in self.structural_location:
            match seg.type:
                case SegmentType.ARTICLE:
                    parts.append(f"Article {seg.value}")
                case SegmentType.ANNEX:
                    parts.append(f"Annex {seg.value}")
                case SegmentType.CHAPTER:
                    parts.append(f"Chapter {seg.value}")
                case SegmentType.SECTION if in_annex:
                    parts.append(f", Section {seg.value}")
                case SegmentType.SECTION:
                    parts.append(f"Section {seg.value}")
                case SegmentType.ANNEX_ITEM if parts and parts[-1].startswith(","):
                    parts.append(f", point {seg.value}")
                case SegmentType.SUBPARAGRAPH:
                    parts.append(f", {_ordinal(seg.value)} subparagraph")
                case SegmentType.PARAGRAPH | SegmentType.ANNEX_AREA:
                    parts.append(f"({seg.value})")
                case SegmentType.POINT | SegmentType.SUB_POINT | SegmentType.ANNEX_ITEM:
                    parts.append(f"({seg.value})")
            in_annex = in_annex or seg.type == SegmentType.ANNEX
        # Collapse the trailing chain onto its parent: Article 5(1)(h),
        # Article 6(3), third subparagraph, Annex I, Section A, point 1.
        out = ""
        for part in parts:
            if part.startswith(("(", ",")) and out:
                out += part
            else:
                out = f"{out} -> {part}" if out else part
        return out

    def text_valid_on(self, on: date) -> bool:
        if on < self.text_valid_from:
            return False
        return self.text_valid_to is None or on < self.text_valid_to


class Corpus(BaseModel):
    """An ingested corpus: provisions plus the rule layer over them."""

    model_config = ConfigDict(extra="forbid")

    source_reference: str
    provisions: list[Provision]
    applicability_rules: list[ApplicabilityRule]

    @classmethod
    def load(cls, directory: Path, source_reference: str = "") -> Corpus:
        """Load a corpus written by `eu-ai-check ingest`."""
        provisions = json.loads((directory / "provisions.json").read_text(encoding="utf-8"))
        rules = json.loads((directory / "applicability_rules.json").read_text(encoding="utf-8"))
        return cls(
            source_reference=source_reference or str(directory),
            provisions=[Provision.model_validate(p) for p in provisions],
            applicability_rules=[ApplicabilityRule.model_validate(r) for r in rules],
        )

    def rules_for(self, logical_provision_id: str) -> list[ApplicabilityRule]:
        return [
            r for r in self.applicability_rules
            if r.legal_provision_id == logical_provision_id
        ]

    def by_logical_id(self) -> dict[str, list[Provision]]:
        grouped: dict[str, list[Provision]] = {}
        for p in self.provisions:
            grouped.setdefault(p.logical_provision_id, []).append(p)
        return grouped

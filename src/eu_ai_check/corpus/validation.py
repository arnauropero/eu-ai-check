"""Ingestion-time validation (SYSTEM_SPEC.md §11 and §12.1).

Everything here is checked **at load time, not at query time**. The reason is the
system's fail-closed contract (§14): a malformed rule and a genuine coverage gap
both surface as `unresolved` during a determination, and once that has happened the
two are nearly impossible to tell apart. Phase 0 is where the difference is still
visible, so it is where the checks belong.

Two independent invariant sets, matching the two independent temporal questions in
§13.1, and deliberately **not** short-circuited into one another:

  §11  text-version integrity -- did the text say this, then?
  §12.1 applicability-rule integrity -- did the rule bind, then?

A provision can pass one and fail the other, so both run and both report.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from enum import StrEnum

from .models import (
    SEGMENT_ABBREV,
    ApplicabilityRule,
    Corpus,
    Provision,
    SegmentType,
)
from .normalize import content_id
from .rules import RECOGNISED_CONDITION_KEYS, RECOGNISED_SCOPE_KEYS


class Severity(StrEnum):
    #: Breaks an invariant. Ingestion must not be treated as successful.
    ERROR = "error"
    #: Permitted, but only because it was declared deliberately. Reported so a
    #: declared gap stays visible rather than becoming invisible precedent.
    DECLARED_GAP = "declared_gap"


@dataclass(frozen=True)
class Finding:
    severity: Severity
    check: str
    subject: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.severity.value}] {self.check}: {self.subject} -- {self.detail}"


@dataclass
class ValidationReport:
    findings: list[Finding] = field(default_factory=list)
    checks_run: list[str] = field(default_factory=list)

    def add(self, severity: Severity, check: str, subject: str, detail: str) -> None:
        self.findings.append(Finding(severity, check, subject, detail))

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.ERROR]

    @property
    def declared_gaps(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.DECLARED_GAP]

    @property
    def ok(self) -> bool:
        """A declared gap does not fail ingestion; an error does."""
        return not self.errors


# --------------------------------------------------------------------------
# §11 -- text-version integrity
# --------------------------------------------------------------------------

def validate_text_versions(provisions: list[Provision], report: ValidationReport) -> None:
    """§11: for one `logical_provision_id`, text-validity ranges must not overlap.

    Gaps between versions are permitted, but only as an explicit "not in force /
    not present" representation -- never as an ingestion accident. V1 ingests a
    single version per provision, so this check is currently trivially satisfied;
    it exists now because the first amendment (the Digital Omnibus amendment to
    Article 5 is the live example) is exactly when an unvalidated overlap would
    start silently returning two conflicting texts for one date.
    """
    report.checks_run.append("text_version_non_overlap")

    by_logical: dict[str, list[Provision]] = defaultdict(list)
    for p in provisions:
        by_logical[p.logical_provision_id].append(p)

    for logical_id, versions in by_logical.items():
        ordered = sorted(versions, key=lambda p: p.text_valid_from)
        for earlier, later in zip(ordered, ordered[1:]):
            if earlier.text_valid_to is None:
                report.add(
                    Severity.ERROR,
                    "text_version_non_overlap",
                    logical_id,
                    f"{earlier.versioned_provision_id} is open-ended "
                    f"(text_valid_to=None) but a later version "
                    f"{later.versioned_provision_id} starts "
                    f"{later.text_valid_from}; the open range overlaps it",
                )
            elif earlier.text_valid_to > later.text_valid_from:
                report.add(
                    Severity.ERROR,
                    "text_version_non_overlap",
                    logical_id,
                    f"{earlier.versioned_provision_id} ends {earlier.text_valid_to}, "
                    f"after {later.versioned_provision_id} starts "
                    f"{later.text_valid_from}",
                )


def validate_identity_model(provisions: list[Provision], report: ValidationReport) -> None:
    """§10: the three identities must stay distinct and internally consistent.

    Checks that `content_id` really is the hash of the stored canonical text (so a
    stale hash cannot silently point an embedding at the wrong text), and that
    `versioned_provision_id` is unique. A repeated `content_id` is *not* an error:
    two versions sharing byte-identical text sharing one embedding is the whole
    point of separating content identity from version identity.
    """
    report.checks_run.append("identity_model")

    seen_versioned: set[str] = set()
    for p in provisions:
        if p.versioned_provision_id in seen_versioned:
            report.add(
                Severity.ERROR,
                "identity_model",
                p.versioned_provision_id,
                "duplicate versioned_provision_id",
            )
        seen_versioned.add(p.versioned_provision_id)

        expected = content_id(p.text)
        if p.content_id != expected:
            report.add(
                Severity.ERROR,
                "identity_model",
                p.versioned_provision_id,
                f"content_id {p.content_id[:12]}... does not match SHA-256 of the "
                f"normalized text ({expected[:12]}...)",
            )


def validate_structural_paths(
    provisions: list[Provision], report: ValidationReport
) -> None:
    """§6: paths must be well-formed and must uniquely identify a provision.

    Depth is deliberately not constrained -- the fixed-depth assumption was
    already falsified by the corpus (Article 16 is 2 levels, Article 5 is 4).
    What is constrained is that a path descends through a legal ordering and that
    no two provisions claim the same path.
    """
    report.checks_run.append("structural_path")

    #: Permitted parent -> child transitions in the Act's own hierarchy.
    allowed: dict[SegmentType, set[SegmentType]] = {
        SegmentType.CHAPTER: {SegmentType.SECTION, SegmentType.ARTICLE},
        SegmentType.SECTION: {SegmentType.ARTICLE},
        SegmentType.ARTICLE: {SegmentType.PARAGRAPH, SegmentType.POINT},
        SegmentType.PARAGRAPH: {SegmentType.POINT},
        SegmentType.POINT: {SegmentType.SUB_POINT},
        SegmentType.SUB_POINT: {SegmentType.SUB_POINT},
        SegmentType.ANNEX: {SegmentType.ANNEX_AREA},
        SegmentType.ANNEX_AREA: {SegmentType.ANNEX_ITEM},
        SegmentType.ANNEX_ITEM: set(),
    }

    seen_paths: dict[tuple[str, ...], str] = {}
    for p in provisions:
        key = tuple(str(s) for s in p.structural_location)
        if key in seen_paths:
            report.add(
                Severity.ERROR,
                "structural_path",
                p.logical_provision_id,
                f"structural_location collides with {seen_paths[key]}",
            )
        seen_paths[key] = p.logical_provision_id

        for parent, child in zip(p.structural_location, p.structural_location[1:]):
            if child.type not in allowed.get(parent.type, set()):
                report.add(
                    Severity.ERROR,
                    "structural_path",
                    p.logical_provision_id,
                    f"illegal path transition {parent.type.value} -> {child.type.value}",
                )


def validate_cross_references(
    provisions: list[Provision], report: ValidationReport
) -> None:
    """§8: a cross-reference edge must point at something, or be reported.

    V1 ingests only Articles 5/16/25/26 and Annex III, so references out to
    un-ingested provisions are expected and are reported as declared gaps rather
    than errors -- the corpus is deliberately partial. What would be an error is a
    reference the system cannot even characterise.
    """
    report.checks_run.append("cross_reference_integrity")

    known = {p.logical_provision_id for p in provisions}
    # An article-level reference resolves if any ingested provision sits under it.
    known_articles = {
        f"aiact:{SEGMENT_ABBREV[seg.type]}_{seg.value}"
        for p in provisions
        for seg in p.structural_location
        if seg.type in (SegmentType.ARTICLE, SegmentType.ANNEX)
    }
    resolvable = known | known_articles

    unresolved: dict[str, set[str]] = defaultdict(set)
    for p in provisions:
        for ref in p.cross_references:
            if ref not in resolvable:
                unresolved[ref].add(p.logical_provision_id)

    for ref, citing in sorted(unresolved.items()):
        report.add(
            Severity.DECLARED_GAP,
            "cross_reference_integrity",
            ref,
            f"referenced by {len(citing)} ingested provision(s) but not in the V1 "
            f"corpus; single-hop resolution will fail closed on it",
        )


# --------------------------------------------------------------------------
# §12.1 -- applicability-rule integrity
# --------------------------------------------------------------------------

def validate_rule_temporal_fields(
    rules: list[ApplicabilityRule], report: ValidationReport
) -> None:
    """§12.1: `applicable_from` present; `applicable_to` strictly after it.

    Field-level ordering is enforced by the model itself, so a malformed rule
    cannot be constructed at all. This check covers what the model cannot see: two
    rules on the same provision and conditions whose windows overlap, which would
    make "was this binding on date X" ambiguous.
    """
    report.checks_run.append("rule_temporal_fields")

    grouped: dict[tuple[str, tuple], list[ApplicabilityRule]] = defaultdict(list)
    for rule in rules:
        key = (rule.legal_provision_id, tuple(sorted(rule.conditions.items())))
        grouped[key].append(rule)

    for (provision_id, conditions), group in grouped.items():
        ordered = sorted(group, key=lambda r: r.applicable_from)
        for earlier, later in zip(ordered, ordered[1:]):
            overlaps = (
                earlier.applicable_to is None
                or earlier.applicable_to > later.applicable_from
            )
            if overlaps:
                report.add(
                    Severity.ERROR,
                    "rule_temporal_fields",
                    f"{earlier.rule_id} / {later.rule_id}",
                    f"two rules for {provision_id} under identical conditions "
                    f"{dict(conditions)} have overlapping windows; applicability on "
                    f"a given date would be ambiguous",
                )


def validate_rule_provision_references(
    provisions: list[Provision], rules: list[ApplicabilityRule],
    report: ValidationReport,
) -> None:
    """§12.1: every rule's `legal_provision_id` must resolve to an ingested
    provision. A rule pointing at nothing is an ingestion failure, not a warning:
    it can never fire, so the obligation it was written to schedule would silently
    never apply."""
    report.checks_run.append("rule_provision_reference")

    known = {p.logical_provision_id for p in provisions}
    for rule in rules:
        if rule.legal_provision_id not in known:
            report.add(
                Severity.ERROR,
                "rule_provision_reference",
                rule.rule_id,
                f"legal_provision_id {rule.legal_provision_id!r} is not an ingested "
                f"provision",
            )


def validate_rule_conditions(
    rules: list[ApplicabilityRule], report: ValidationReport
) -> None:
    """§12.1: `conditions` and `scope` non-empty, using only recognised V1 keys.

    An empty or unrecognised condition set is flagged rather than accepted,
    because at query time it fails closed into `unresolved` in a way that looks
    identical to a genuine coverage gap.
    """
    report.checks_run.append("rule_conditions_wellformed")

    for rule in rules:
        for name, value, recognised in (
            ("conditions", rule.conditions, RECOGNISED_CONDITION_KEYS),
            ("scope", rule.scope, RECOGNISED_SCOPE_KEYS),
        ):
            if not value:
                report.add(
                    Severity.ERROR,
                    "rule_conditions_wellformed",
                    rule.rule_id,
                    f"{name} is empty",
                )
                continue
            unknown = set(value) - recognised
            if unknown:
                report.add(
                    Severity.ERROR,
                    "rule_conditions_wellformed",
                    rule.rule_id,
                    f"{name} uses unrecognised key(s) {sorted(unknown)}; "
                    f"recognised in V1: {sorted(recognised)}",
                )


def validate_no_orphan_obligations(
    provisions: list[Provision], rules: list[ApplicabilityRule],
    report: ValidationReport,
) -> None:
    """§12.1: no obligation or prohibition provision silently carries zero rules.

    Such a provision is the source of a false `unresolved` later. Phase 0 is where
    it should surface, not Phase 3. A provision may legitimately have no rule, but
    only if the gap is declared on the provision itself via
    `applicability_gap_reason` -- which turns an invisible omission into a
    reviewable statement.
    """
    report.checks_run.append("no_orphan_obligations")

    with_rules = {r.legal_provision_id for r in rules}
    for p in provisions:
        if not p.requires_applicability_rule:
            continue
        if p.logical_provision_id in with_rules:
            continue
        if p.applicability_gap_reason:
            report.add(
                Severity.DECLARED_GAP,
                "no_orphan_obligations",
                p.logical_provision_id,
                f"{p.provision_type.value} with no applicability rule; declared "
                f"intentional: {p.applicability_gap_reason}",
            )
        else:
            report.add(
                Severity.ERROR,
                "no_orphan_obligations",
                p.logical_provision_id,
                f"{p.provision_type.value} provision has zero ApplicabilityRules "
                f"and no declared gap reason; would fail closed as a false "
                f"'unresolved' at determination time",
            )


def validate_corpus(corpus: Corpus) -> ValidationReport:
    """Run every Phase 0 invariant. The two invariant sets are run independently
    and neither short-circuits the other (§12.1)."""
    report = ValidationReport()

    # §11 / §10 / §6 / §8 -- provision-side integrity.
    validate_text_versions(corpus.provisions, report)
    validate_identity_model(corpus.provisions, report)
    validate_structural_paths(corpus.provisions, report)
    validate_cross_references(corpus.provisions, report)

    # §12.1 -- rule-side integrity.
    validate_rule_temporal_fields(corpus.applicability_rules, report)
    validate_rule_provision_references(
        corpus.provisions, corpus.applicability_rules, report
    )
    validate_rule_conditions(corpus.applicability_rules, report)
    validate_no_orphan_obligations(
        corpus.provisions, corpus.applicability_rules, report
    )

    return report

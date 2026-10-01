"""Map the parsed source tree onto canonical `Provision` records.

This module owns the *legal* chunking rule; `parser.py` owns the markup. The rule
implements SYSTEM_SPEC.md §5's definition of the canonical unit:

    the smallest legally meaningful provision that can be independently retrieved
    and cited without losing the conditions required to interpret it

Two parts of that definition do real work here.

**"smallest"** -- a node is canonical when it has no enumerated children, with one
exception: a node whose children are all `sub_point`s stays whole. Article 5(1)(h)
is the verified case (§7.1): its exceptions (i)-(iii) are not independently
interpretable, so splitting them would let the system state an exception without
the prohibition it excepts from.

**"without losing the conditions required to interpret it"** -- this is not
satisfied by the node's own text alone. Article 5(1)(a) read in isolation is a
bare noun phrase ("the placing on the market ... of an AI system that deploys
subliminal techniques ..."); the operative verb lives in its parent's lead-in
("The following AI practices shall be prohibited:"). A canonical provision's text
therefore carries:

  * the **lead-in** -- ancestor own-text appearing before the node in source
    order, which supplies the operative stem; and
  * any **qualifier** -- ancestor own-text appearing after the node that
    explicitly names it. Article 5(1) ends with "Point (h) of the first
    subparagraph is without prejudice to Article 9 of Regulation (EU) 2016/679",
    which qualifies point (h) and no other point, so it is attached to (h) alone
    rather than to all of (a)-(h).

`content_id` hashes this assembled canonical text, because that is the text that
actually gets embedded (§10).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from .models import (
    SEGMENT_ABBREV,
    Provision,
    ProvisionType,
    SegmentType,
    StructuralSegment,
)
from .normalize import content_id, normalize_text
from .parser import ArticleNode, RawNode

#: Regulation (EU) 2024/1689 was published in the OJ on 12 July 2024 and entered
#: into force on the twentieth day following publication (Article 113, first
#: subparagraph). Text validity starts there. This is *entry into force*, which is
#: a different question from when obligations begin to apply -- that lives in the
#: ApplicabilityRule layer (§13.1), never inferred from this date.
ENTRY_INTO_FORCE = date(2024, 8, 1)

ID_PREFIX = "aiact"

#: Which articles V1 ingests, and the legal character of their operative units
#: (PROJECT_PLAN.md §3).
V1_ARTICLES: dict[str, ProvisionType] = {
    "5": ProvisionType.PROHIBITION,
    # Article 6 carries the high-risk classification rules. It is in V1 scope
    # because Phase 0 ingestion showed the rest of the corpus depends on it: 27 of
    # the 73 provisions ingested without it cross-referenced it, and the
    # applicability conditions on Articles 16/26 distinguish Annex III high-risk
    # (Article 6(2)) from Article 6(1) high-risk -- a distinction Article 6 is what
    # defines. Its default character is `scope`: it classifies, it does not itself
    # impose a duty.
    "6": ProvisionType.SCOPE,
    "16": ProvisionType.OBLIGATION,
    "25": ProvisionType.OBLIGATION,
    "26": ProvisionType.OBLIGATION,
}
V1_ANNEXES: dict[str, ProvisionType] = {
    "III": ProvisionType.ANNEX_CATEGORY,
}

#: Per-paragraph type overrides, where an article's paragraphs do not all share the
#: article's default character. Keyed article -> paragraph -> type.
#:
#: Article 5: paragraphs 2-8 set out the authorisation procedure and safeguards
#: conditioning the point (h) exception. They are not themselves prohibitions.
#:
#: Article 6 is genuinely heterogeneous, so typing it uniformly would misdescribe
#: it. Paragraphs 1 and 2 are the two high-risk classification routes (`scope`).
#: Paragraph 3 opens "By derogation from paragraph 2" and lets an Annex III system
#: be found *not* high-risk -- a real determination branch, so `exception`.
#: Paragraph 4 is a genuine provider duty ("A provider ... shall document its
#: assessment"), so `obligation`. Paragraph 8 constrains future amendments rather
#: than regulating an AI system, so `scope`.
_PARAGRAPH_TYPE_OVERRIDES: dict[str, dict[str, ProvisionType]] = {
    "5": {
        para: ProvisionType.EXCEPTION
        for para in ("2", "3", "4", "5", "6", "7", "8")
    },
    "6": {
        "1": ProvisionType.SCOPE,
        "2": ProvisionType.SCOPE,
        "3": ProvisionType.EXCEPTION,
        "4": ProvisionType.OBLIGATION,
        "5": ProvisionType.OBLIGATION,
        "6": ProvisionType.OBLIGATION,
        "7": ProvisionType.OBLIGATION,
        "8": ProvisionType.SCOPE,
    },
}

#: Obligations that V1 deliberately does not schedule, with the reason recorded on
#: the provision so §12.1's no-orphan check reports a declared gap instead of an
#: error. Article 6(5)-(7) place duties on the Commission (issuing guidelines,
#: adopting delegated acts), not on a provider or deployer. V1 covers the
#: provider/deployer determination surface only (PROJECT_PLAN.md §4), so scheduling
#: them would mean inventing an actor role the system does not model.
_DECLARED_APPLICABILITY_GAPS: dict[str, str] = {
    "aiact:art_6:para_5": (
        "duty on the Commission (issue guidelines), not on a provider or deployer; "
        "V1 covers provider/deployer obligations only (PROJECT_PLAN.md §4)"
    ),
    "aiact:art_6:para_6": (
        "empowerment of the Commission to adopt delegated acts; not a "
        "provider/deployer duty (PROJECT_PLAN.md §4)"
    ),
    "aiact:art_6:para_7": (
        "duty on the Commission to adopt delegated acts; not a provider/deployer "
        "duty (PROJECT_PLAN.md §4)"
    ),
}


# A trailing colon marks an introductory lead-in rather than a standalone rule.
_LEAD_IN_RE = re.compile(r":\s*$")

# Cross-references to other provisions of this Regulation (§8). Single-hop,
# article-level: "Article 6(2)" -> the Article 6 logical id. References to *other*
# instruments ("Article 9 of Regulation (EU) 2016/679") are deliberately excluded,
# since they are outside this corpus and cannot be resolved against it.
_CROSS_REF_RE = re.compile(
    r"\bArticle\s+(\d+)(?:\(\d+\))?(?!\s+of\s+(?:Regulation|Directive|Decision))"
)
_ANNEX_REF_RE = re.compile(
    r"\bAnnex\s+([IVXLC]+)(?!\s+of\s+(?:Regulation|Directive|Decision))"
)


@dataclass
class IngestIssue:
    """A deviation worth surfacing rather than silently absorbing."""

    kind: str
    detail: str


def _logical_id(path: list[StructuralSegment]) -> str:
    """Build a stable legal identity from the structural path.

    Chapter and section are deliberately excluded: they are navigational context,
    and including them would change the id if the Act were ever restructured
    without touching the provision itself.
    """
    parts = [
        f"{SEGMENT_ABBREV[seg.type]}_{seg.value}"
        for seg in path
        if seg.type not in (SegmentType.CHAPTER, SegmentType.SECTION)
    ]
    return f"{ID_PREFIX}:" + ":".join(parts)


def _versioned_id(logical_id: str, valid_from: date) -> str:
    """One specific text version of a logical provision (§10)."""
    return f"{logical_id}@{valid_from.isoformat()}"


def _is_canonical(node: RawNode) -> bool:
    """Whether `node` is the smallest independently interpretable unit (§5).

    Terminal nodes are canonical. A node whose children are all sub-points is
    also canonical and stays whole -- the Article 5(1)(h) rule (§7.1).
    """
    children = node.children
    if not children:
        return True
    return all(c.level == SegmentType.SUB_POINT for c in children)


def _is_lead_in(text: str) -> bool:
    """A fragment that introduces an enumerated list rather than standing alone."""
    return bool(_LEAD_IN_RE.search(text))


def _qualifies(text: str, marker: str, level: SegmentType) -> bool:
    """Whether a trailing ancestor fragment explicitly names this node.

    Matches the Act's own referring style, e.g. "Point (h) of the first
    subparagraph ...". Used so a scoped savings clause attaches only to the point
    it actually scopes.
    """
    if level in (SegmentType.POINT, SegmentType.SUB_POINT):
        return bool(re.search(rf"\bPoints?\s+\({re.escape(marker)}\)", text, re.I))
    if level == SegmentType.PARAGRAPH:
        return bool(re.search(rf"\bparagraph\s+{re.escape(marker)}\b", text, re.I))
    return False


def _canonical_text(node: RawNode, ancestors: list[RawNode]) -> str:
    """Assemble the canonical text: ancestor lead-ins, the node itself, then any
    ancestor qualifier that names the node (see module docstring)."""
    lead_ins: list[str] = []
    qualifiers: list[str] = []

    for ancestor in ancestors:
        # Walk the ancestor's content in source order, tracking whether we have
        # passed the branch containing `node`. Fragments before it are lead-ins
        # (the operative stem); fragments after it can only be qualifiers.
        seen_node_branch = False
        for item in ancestor.content:
            if isinstance(item, RawNode):
                if item is node or any(d is node for d in item.iter_descendants()):
                    seen_node_branch = True
                continue
            if not seen_node_branch:
                lead_ins.append(item)
            elif _qualifies(item, node.marker, node.level):
                qualifiers.append(item)

    parts = [*lead_ins, node.full_text(), *qualifiers]
    return normalize_text(" ".join(p for p in parts if p))


def _cross_references(text: str, self_article: str | None) -> list[str]:
    """Extract single-hop, in-corpus cross-references as graph edges (§8).

    Referenced text is never inlined into the citing provision -- that would
    duplicate content and let the copy drift after an amendment.
    """
    refs: list[str] = []
    for num in _CROSS_REF_RE.findall(text):
        if num == self_article:
            continue
        refs.append(f"{ID_PREFIX}:art_{num}")
    for numeral in _ANNEX_REF_RE.findall(text):
        refs.append(f"{ID_PREFIX}:anx_{numeral}")
    # Preserve first-seen order, drop duplicates.
    return list(dict.fromkeys(refs))


def _provision_type(article: ArticleNode, path: list[StructuralSegment],
                    default: ProvisionType) -> ProvisionType:
    """The provision's legal character: the article default, unless the paragraph
    it sits under is overridden (see `_PARAGRAPH_TYPE_OVERRIDES`).

    A point inherits its paragraph's type, because a lettered point of a
    derogation is part of that derogation.
    """
    if article.is_annex:
        return default
    overrides = _PARAGRAPH_TYPE_OVERRIDES.get(article.number)
    if not overrides:
        return default
    para = next((s.value for s in path if s.type == SegmentType.PARAGRAPH), None)
    return overrides.get(para, default) if para else default


def ingest_article(article: ArticleNode) -> tuple[list[Provision], list[IngestIssue]]:
    """Walk one article or annex and emit its canonical provisions."""
    default_type = (
        V1_ANNEXES[article.number] if article.is_annex
        else V1_ARTICLES[article.number]
    )
    provisions: list[Provision] = []
    issues: list[IngestIssue] = []

    base: list[StructuralSegment] = []
    if article.chapter:
        base.append(
            StructuralSegment(type=SegmentType.CHAPTER, value=article.chapter)
        )
    if article.section:
        base.append(
            StructuralSegment(type=SegmentType.SECTION, value=article.section)
        )
    base.append(
        StructuralSegment(
            type=SegmentType.ANNEX if article.is_annex else SegmentType.ARTICLE,
            value=article.number,
        )
    )

    def walk(node: RawNode, path: list[StructuralSegment],
             ancestors: list[RawNode]) -> None:
        if _is_canonical(node):
            text = _canonical_text(node, ancestors)
            if not text:
                issues.append(
                    IngestIssue("empty_provision", f"{_logical_id(path)} has no text")
                )
                return
            logical_id = _logical_id(path)
            ptype = _provision_type(article, path, default_type)
            provisions.append(
                Provision(
                    logical_provision_id=logical_id,
                    versioned_provision_id=_versioned_id(logical_id, ENTRY_INTO_FORCE),
                    content_id=content_id(text),
                    provision_type=ptype,
                    structural_location=path,
                    text=text,
                    heading=article.heading,
                    text_valid_from=ENTRY_INTO_FORCE,
                    text_valid_to=None,
                    cross_references=_cross_references(
                        text, None if article.is_annex else article.number
                    ),
                    applicability_gap_reason=_DECLARED_APPLICABILITY_GAPS.get(
                        logical_id
                    ),
                )
            )
            return

        for child in node.children:
            walk(
                child,
                [*path, StructuralSegment(type=child.level, value=child.marker)],
                [*ancestors, node],
            )

    walk(article.root, base, [])
    return provisions, issues


def ingest_corpus(
    articles: list[ArticleNode],
) -> tuple[list[Provision], list[IngestIssue]]:
    """Ingest the V1 corpus: Articles 5, 16, 25, 26 and Annex III."""
    provisions: list[Provision] = []
    issues: list[IngestIssue] = []
    seen: dict[str, ArticleNode] = {}

    for article in articles:
        wanted = (
            article.number in V1_ANNEXES if article.is_annex
            else article.number in V1_ARTICLES
        )
        if not wanted:
            continue
        key = ("anx" if article.is_annex else "art") + article.number
        if key in seen:
            issues.append(IngestIssue("duplicate_source_node", key))
            continue
        seen[key] = article
        got, art_issues = ingest_article(article)
        provisions.extend(got)
        issues.extend(art_issues)

    expected = {f"art{n}" for n in V1_ARTICLES} | {f"anx{n}" for n in V1_ANNEXES}
    for missing in sorted(expected - set(seen)):
        issues.append(IngestIssue("missing_source_node", missing))

    return provisions, issues

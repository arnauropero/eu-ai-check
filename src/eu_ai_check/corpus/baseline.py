"""Phase 0 measured baselines (PROJECT_PLAN.md §6, SYSTEM_SPEC.md §18).

Phase 0's baseline deliverable is deliberately scoped: it establishes that
*numbers exist to set targets against*, not that the targets are met. The §18
metric categories split by what can honestly be measured yet:

**Measurable now** (corpus-side, reported here): how many citable units exist and
at what granularity, how much text each carries (the input to embedding and
chunking decisions in Phase 1), how much index-time deduplication the `content_id`
layer actually buys, rule coverage over operative provisions, and what share of
cross-reference edges resolve inside the V1 corpus.

**Not measurable yet** (deferred, with the component that unblocks them): retrieval
Recall@k and Precision@k need the Phase 1 hybrid index and the hand-built query
set; extraction field accuracy needs the Phase 2 extractor and its labelled set.
Reporting a number for these now would mean inventing one, which is the thing the
plan says not to do -- targets are locked progressively against real baselines.

The dedup figure is worth reading carefully: it is near-zero on a single-version
corpus and *should* be, because no provision has been amended yet. It is reported
now so the first amendment (the Digital Omnibus amendment to Article 5 is the live
case) has a before-number to move against -- that is when separating `content_id`
from `versioned_provision_id` starts paying.
"""

from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass, field

from .models import Corpus, ProvisionType, SegmentType


@dataclass
class Baseline:
    """Measured corpus-side baselines, plus an explicit record of what is not yet
    measurable and why."""

    provisions: int
    applicability_rules: int
    by_granularity: dict[str, int]
    by_provision_type: dict[str, int]
    legal_depths: dict[int, int]
    text_chars: dict[str, float]
    distinct_content_ids: int
    dedup_ratio: float
    operative_provisions: int
    operative_with_rules: int
    rule_coverage: float
    cross_reference_edges: int
    cross_references_resolved: int
    cross_reference_resolution: float
    deferred: dict[str, str] = field(default_factory=dict)

    def render(self) -> str:
        lines = [
            "Phase 0 measured baselines",
            "=" * 60,
            "",
            "Corpus size and granularity",
            f"  citable provisions           {self.provisions}",
            f"  applicability rules          {self.applicability_rules}",
            "  by granularity:",
        ]
        for level, count in sorted(self.by_granularity.items()):
            lines.append(f"    {level:<24} {count}")
        lines.append("  by provision type:")
        for ptype, count in sorted(self.by_provision_type.items()):
            lines.append(f"    {ptype:<24} {count}")
        lines.append("  legal depth below article/annex:")
        for depth, count in sorted(self.legal_depths.items()):
            lines.append(f"    {depth} level(s){'':<13} {count}")

        lines += [
            "",
            "Canonical text size (input to Phase 1 chunking decisions)",
            f"  min / median / max chars     {int(self.text_chars['min'])} / "
            f"{int(self.text_chars['median'])} / {int(self.text_chars['max'])}",
            f"  mean chars                   {self.text_chars['mean']:.0f}",
            f"  provisions over 2000 chars   {int(self.text_chars['over_2000'])}",
            "",
            "Index-time deduplication (§10, §15)",
            f"  distinct content_ids         {self.distinct_content_ids} of "
            f"{self.provisions}",
            f"  dedup ratio                  {self.dedup_ratio:.3f}  "
            f"(expected ~0 on a single-version corpus)",
            "",
            "Rule coverage (§12.1)",
            f"  operative provisions         {self.operative_provisions} "
            f"(obligation + prohibition)",
            f"  with >=1 applicability rule  {self.operative_with_rules}",
            f"  coverage                     {self.rule_coverage:.3f}",
            "",
            "Cross-reference resolution (§8)",
            f"  distinct edges               {self.cross_reference_edges}",
            f"  resolvable in V1 corpus      {self.cross_references_resolved}",
            f"  resolution rate              {self.cross_reference_resolution:.3f}",
            "",
            "Deferred metric categories (§18) -- no number invented here",
        ]
        for metric, reason in sorted(self.deferred.items()):
            lines.append(f"  {metric}")
            lines.append(f"    blocked on: {reason}")
        return "\n".join(lines)


def _legal_depth(provision) -> int:
    context = (
        SegmentType.CHAPTER,
        SegmentType.SECTION,
        SegmentType.ARTICLE,
        SegmentType.ANNEX,
    )
    return len([s for s in provision.structural_location if s.type not in context])


def measure(corpus: Corpus) -> Baseline:
    provisions = corpus.provisions
    lengths = [len(p.text) for p in provisions]

    content_ids = {p.content_id for p in provisions}
    operative = [p for p in provisions if p.requires_applicability_rule]
    with_rules = {r.legal_provision_id for r in corpus.applicability_rules}

    edges = {ref for p in provisions for ref in p.cross_references}
    resolvable = {p.logical_provision_id for p in provisions}
    from .models import SEGMENT_ABBREV

    resolvable |= {
        f"aiact:{SEGMENT_ABBREV[seg.type]}_{seg.value}"
        for p in provisions
        for seg in p.structural_location
        if seg.type in (SegmentType.ARTICLE, SegmentType.ANNEX)
    }
    resolved = edges & resolvable

    return Baseline(
        provisions=len(provisions),
        applicability_rules=len(corpus.applicability_rules),
        by_granularity=dict(
            Counter(p.structural_location[-1].type.value for p in provisions)
        ),
        by_provision_type=dict(Counter(p.provision_type.value for p in provisions)),
        legal_depths=dict(Counter(_legal_depth(p) for p in provisions)),
        text_chars={
            "min": min(lengths),
            "median": statistics.median(lengths),
            "max": max(lengths),
            "mean": statistics.fmean(lengths),
            "over_2000": sum(1 for n in lengths if n > 2000),
        },
        distinct_content_ids=len(content_ids),
        dedup_ratio=1 - len(content_ids) / len(provisions),
        operative_provisions=len(operative),
        operative_with_rules=sum(
            1 for p in operative if p.logical_provision_id in with_rules
        ),
        rule_coverage=(
            sum(1 for p in operative if p.logical_provision_id in with_rules)
            / len(operative)
            if operative else 0.0
        ),
        cross_reference_edges=len(edges),
        cross_references_resolved=len(resolved),
        cross_reference_resolution=len(resolved) / len(edges) if edges else 0.0,
        deferred={
            "retrieval Recall@k / Precision@k (vector-only vs hybrid, "
            "with/without temporal filtering)":
                "Phase 1 hybrid index + hand-built query set with known-correct "
                "versioned_provision_id ground truth",
            "extraction field-level accuracy":
                "Phase 2 extractor + hand-labelled free-text eval set",
            "determination correctness across the four applicability outcomes":
                "Phase 3 GroundedDetermination + rule-matching engine",
        },
    )

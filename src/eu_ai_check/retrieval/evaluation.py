"""Retrieval evaluation harness (PROJECT_PLAN.md §6 Phase 1, SYSTEM_SPEC.md §18).

Two comparisons, each on the corpus where it means something:

1. **Vector-only vs BM25-only vs hybrid, with and without reranking**, on the
   real corpus. Reported per query category, because the Phase 1 acceptance
   criterion is specifically about `terminology` queries (article numbers,
   Annex III points), where BM25 is expected to matter most. An overall average
   could hide a terminology regression behind a use-case gain.

2. **Pre-filter vs post-filter vs no temporal filter**, on the real corpus plus
   the synthetic version fixture. On the real single-version corpus the three
   arms are identical by construction for any date after entry into force, so
   measuring them there would report a meaningless tie.

**Pre-registered vs post-hoc queries.** Queries flagged `post_hoc` (the bare- and
variant-citation terminology sets) were added after the first run had been seen.
The report always shows the original pre-registered queries as their own slice, so
a post-hoc gain can never be read as if it had been planned.

**k = 1 for the temporal arms.** The synthetic fixture adds one extra version per
amended provision, so an ineligible version can take at most one slot ahead of
the eligible one. Candidate starvation from post-filtering therefore shows at
k = 1 and largely washes out by k = 5. Both are reported; the k = 1 number is the
one the fixture can actually demonstrate.

Ground truth is a set of logical ids per query, resolved to the
`versioned_provision_id` valid on the query's `relevant_date`. Retrieval is
therefore scored at *version* level: returning the right article in the wrong
version counts as a miss, which is exactly what makes candidate starvation
visible.
"""

from __future__ import annotations

import json
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from ..corpus.models import Provision
from ..corpus.normalize import content_id
from ..corpus.validation import ValidationReport, validate_text_versions
from .retriever import Mode, RetrievalConfig, Retriever, TemporalMode

KS = (1, 5, 10)


@dataclass(frozen=True)
class EvalQuery:
    id: str
    category: str
    query: str
    relevant_date: date
    relevant: tuple[str, ...]
    post_hoc: bool = False


def load_queries(path: Path) -> list[EvalQuery]:
    raw = json.loads(path.read_text(encoding="utf-8"))["queries"]
    return [
        EvalQuery(
            id=q["id"],
            category=q["category"],
            query=q["query"],
            relevant_date=date.fromisoformat(q["relevant_date"]),
            relevant=tuple(q["relevant"]),
            post_hoc=q.get("post_hoc", False),
        )
        for q in raw
    ]


def apply_synthetic_versions(provisions: Sequence[Provision], path: Path) -> list[Provision]:
    """Close each amended provision's current version on the amendment date and
    add a successor version carrying the edited text.

    The result must pass the same §11 non-overlap check as real ingestion. A
    fixture that broke version integrity would be measuring a corpus the real
    pipeline could never produce.
    """
    fixture = json.loads(path.read_text(encoding="utf-8"))
    on = date.fromisoformat(fixture["amendment_date"])
    edits = {a["logical_provision_id"]: a["replace"] for a in fixture["amendments"]}

    out: list[Provision] = []
    for p in provisions:
        if p.logical_provision_id not in edits:
            out.append(p)
            continue
        old, new = edits.pop(p.logical_provision_id)
        if old not in p.text:
            raise ValueError(f"synthetic edit for {p.logical_provision_id}: {old!r} not in text")
        text = p.text.replace(old, new)
        out.append(p.model_copy(update={"text_valid_to": on}))
        out.append(
            p.model_copy(
                update={
                    "versioned_provision_id": f"{p.logical_provision_id}@{on.isoformat()}",
                    "content_id": content_id(text),
                    "text": text,
                    "text_valid_from": on,
                    "text_valid_to": None,
                }
            )
        )
    if edits:
        raise ValueError(f"synthetic edits target unknown provisions: {sorted(edits)}")

    report = ValidationReport()
    validate_text_versions(out, report)
    if not report.ok:
        raise ValueError(f"synthetic fixture breaks version integrity: {report.errors}")
    return out


def expected_versions(query: EvalQuery, provisions: Sequence[Provision]) -> set[str]:
    return {
        p.versioned_provision_id
        for p in provisions
        if p.logical_provision_id in query.relevant and p.text_valid_on(query.relevant_date)
    }


@dataclass
class QueryResult:
    query: EvalQuery
    expected: set[str]
    retrieved: dict[int, list[str]]
    #: Results whose text was not valid on relevant_date -- a citation of a
    #: version that did not exist (or no longer existed) on that date.
    invalid: dict[int, int]

    def recall(self, k: int) -> float:
        return len(self.expected & set(self.retrieved[k])) / len(self.expected)

    def precision(self, k: int) -> float:
        return len(self.expected & set(self.retrieved[k])) / k

    def reciprocal_rank(self) -> float:
        for i, vid in enumerate(self.retrieved[max(KS)], start=1):
            if vid in self.expected:
                return 1.0 / i
        return 0.0


@dataclass
class ArmResult:
    config: RetrievalConfig
    results: list[QueryResult] = field(default_factory=list)

    def _in(self, r: QueryResult, category: str | None) -> bool:
        if category is None:
            return True
        if category == PRE_REGISTERED:
            return not r.query.post_hoc
        return r.query.category == category

    def scored(self, category: str | None = None) -> list[QueryResult]:
        return [r for r in self.results if r.expected and self._in(r, category)]

    def summary(self, category: str | None = None) -> dict[str, float]:
        rs = self.scored(category)
        if not rs:
            return {}
        out = {}
        for k in KS:
            out[f"R@{k}"] = statistics.fmean(r.recall(k) for r in rs)
            out[f"P@{k}"] = statistics.fmean(r.precision(k) for r in rs)
            out[f"invalid@{k}"] = sum(r.invalid[k] for r in self.results if self._in(r, category))
        out["MRR"] = statistics.fmean(r.reciprocal_rank() for r in rs)
        return out


def run_arm(
    retriever: Retriever,
    queries: Sequence[EvalQuery],
    config: RetrievalConfig,
) -> ArmResult:
    arm = ArmResult(config)
    for q in queries:
        retrieved: dict[int, list[str]] = {}
        invalid: dict[int, int] = {}
        for k in KS:
            hits = retriever.retrieve(q.query, q.relevant_date, k=k, config=config)
            retrieved[k] = [h.provision.versioned_provision_id for h in hits]
            invalid[k] = sum(1 for h in hits if not h.provision.text_valid_on(q.relevant_date))
        arm.results.append(
            QueryResult(q, expected_versions(q, list(retriever.provisions.values())), retrieved, invalid)
        )
    return arm


#: Report slice: the queries written before the first run (no `post_hoc` flag).
PRE_REGISTERED = "pre_registered"

RANKING_ARMS = [
    RetrievalConfig(mode=m, rerank=r, citation_priority=c)
    for r in (False, True)
    for m, c in ((Mode.VECTOR, False), (Mode.BM25, False), (Mode.HYBRID, False), (Mode.HYBRID, True))
]

TEMPORAL_ARMS = [
    RetrievalConfig(mode=m, rerank=r, temporal=t, citation_priority=False)
    for m, r in ((Mode.VECTOR, False), (Mode.HYBRID, False))
    for t in (TemporalMode.PRE_FILTER, TemporalMode.POST_FILTER, TemporalMode.NONE)
]


RANKING_COLS = ["R@1", "R@5", "R@10", "P@5", "P@10", "MRR"]
TEMPORAL_COLS = ["R@1", "R@5", "MRR", "invalid@1", "invalid@5"]

RANKING_SLICES: list[tuple[str | None, str]] = [
    ("terminology", "terminology, original (topical words, pre-registered)"),
    ("terminology_bare", "terminology_bare (POST-HOC: bare citations)"),
    ("terminology_variant", "terminology_variant (POST-HOC: other citation styles)"),
    ("use_case", "use_case (pre-registered)"),
    (PRE_REGISTERED, "all pre-registered real-corpus queries (the original 48)"),
    (None, "all real-corpus queries incl. post-hoc"),
]


def _table(arms: Sequence[ArmResult], category: str | None, title: str,
           cols: Sequence[str] = RANKING_COLS) -> list[str]:
    n = len(arms[0].scored(category)) if arms else 0
    lines = [f"{title}  (n={n})", f"  {'arm':<34}" + "".join(f"{c:>10}" for c in cols)]
    for arm in arms:
        s = arm.summary(category)
        cells = "".join(
            f"{int(s[c]):>10}" if c.startswith("invalid") else f"{s[c]:>10.3f}" for c in cols
        )
        lines.append(f"  {arm.config.label:<34}{cells}")
    return lines


def render_report(
    ranking: Sequence[ArmResult],
    temporal: Sequence[ArmResult],
    *,
    show_misses: bool = False,
) -> str:
    lines = ["Phase 1 retrieval evaluation", "=" * 96, ""]
    lines.append("1. Ranking arms, real corpus, temporal pre-filter on")
    lines.append("   (+cite = exact-citation priority, the adopted design, §7.4; arms without it are plain RRF)")
    for category, title in RANKING_SLICES:
        lines.append("")
        lines += _table(ranking, category, f"-- {title}")

    lines += ["", "2. Temporal arms, real corpus + SYNTHETIC version fixture"]
    lines += [""] + _table(temporal, "temporal", "-- temporal queries (amended wording)", TEMPORAL_COLS)
    lines.append("")
    lines.append("  pre-entry-into-force queries (correct answer: no results)")
    for arm in temporal:
        empty = [r for r in arm.results if not r.expected]
        ok = sum(1 for r in empty if not r.retrieved[5])
        lines.append(f"    {arm.config.label:<32} {ok}/{len(empty)} correctly empty")

    if show_misses:
        lines += ["", "Misses (expected version not in top-5)"]
        for arm in [*ranking, *temporal]:
            for r in arm.scored():
                if r.recall(5) < 1:
                    lines.append(
                        f"  [{arm.config.label}] {r.query.id}: expected "
                        f"{sorted(r.expected)} got {r.retrieved[5]}"
                    )
    return "\n".join(lines)


def summary_json(ranking: Sequence[ArmResult], temporal: Sequence[ArmResult]) -> dict:
    def arms(rs: Sequence[ArmResult], categories: Sequence[str | None]) -> dict:
        return {
            a.config.label: {c or "all": a.summary(c) for c in categories}
            for a in rs
        }

    return {
        "ranking": arms(ranking, [c for c, _ in RANKING_SLICES]),
        "temporal": arms(temporal, ("temporal",)),
    }

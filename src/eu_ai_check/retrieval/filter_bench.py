"""SYSTEM_SPEC.md §21: does Qdrant's *physical* filtered search keep recall under
the temporal pre-filter? Needs a Qdrant server (`--qdrant-url`).

Embedded local mode answers every query by exact scan, so it can only prove the
filter is logically right. Two separate questions remain, and they get separate
parts here:

**Part A -- the real corpus.** For every eval query on its own `relevant_date`,
compare server HNSW + payload filter against server exact search with the same
filter, and check that embedded mode matches server exact. At ~100-200 points
Qdrant would never build an HNSW graph by default (its indexing threshold is far
above this corpus size), so the HNSW arm *forces* graph construction and graph
traversal. The default-config arm reports what a server really does at this
size.

**Part B -- selectivity sweep.** The corpus is too small to stress the graph, and
almost every date admits ~all of it. Part B builds a 20k-point collection of
seeded random unit vectors with validity windows arranged so the *same*
`eligibility_filter` admits a chosen fraction of points (100% down to 0.1%), then
measures Recall@10 against exact search under four configurations:

    payload index on/off   x   Qdrant default query planner / HNSW forced

Random vectors are a deliberately hard, structure-free case -- recall on real
embeddings is usually higher -- so Part B bounds behaviour rather than predicting
production numbers. Unfiltered recall is reported as the reference point.
"""

from __future__ import annotations

import statistics
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
from qdrant_client import models

from .chunking import SubChunk
from .vector_store import QdrantVectorStore

#: Forces graph construction (indexing_threshold in KB) and graph traversal under
#: a filter (full_scan_threshold in KB, server minimum 10 = ~3 bge vectors: below it, Qdrant answers filtered queries
#: from the payload index by exact scan instead of the graph).
FORCE_HNSW = models.HnswConfigDiff(m=16, ef_construct=100, full_scan_threshold=10)
FORCE_INDEXING = models.OptimizersConfigDiff(indexing_threshold=1, default_segment_number=1)


def wait_until_indexed(store: QdrantVectorStore, expect_graph: bool, timeout: float = 300) -> int:
    """Block until optimisation finishes; return indexed_vectors_count. With
    `expect_graph`, raise if no graph was built -- a 'HNSW' arm that silently ran
    exact search would make the whole comparison meaningless."""
    deadline = time.monotonic() + timeout
    while True:
        info = store.client.get_collection(store.collection)
        done = info.status == models.CollectionStatus.GREEN
        if done and (not expect_graph or (info.indexed_vectors_count or 0) > 0):
            return info.indexed_vectors_count or 0
        if time.monotonic() > deadline:
            raise TimeoutError(f"{store.collection}: not indexed (status={info.status}, "
                               f"indexed={info.indexed_vectors_count})")
        time.sleep(0.5)


def overlap(approx: Sequence[str], exact: Sequence[str], k: int) -> float:
    truth = set(exact[:k])
    return len(truth & set(approx[:k])) / len(truth) if truth else 1.0


# ---------------------------------------------------------------------------
# Part A
# ---------------------------------------------------------------------------

@dataclass
class CorpusResult:
    label: str
    points: int
    indexed_vectors: int
    queries: int
    recall_at_10: float
    recall_at_30: float
    min_recall_at_10: float
    embedded_matches_exact: int


def bench_corpus(
    label: str,
    chunks: Sequence[SubChunk],
    vectors: Sequence[np.ndarray],
    queries: Sequence[tuple[np.ndarray, date]],
    url: str,
) -> list[CorpusResult]:
    embedded = QdrantVectorStore(len(vectors[0]))
    embedded.add(chunks, vectors)
    out = []
    for arm, hnsw, opt, graph in (
        ("server default config", None, None, False),
        ("server HNSW forced", FORCE_HNSW, FORCE_INDEXING, True),
    ):
        store = QdrantVectorStore(
            len(vectors[0]), url=url, collection=f"bench_{label}_{graph}",
            hnsw_config=hnsw, optimizers_config=opt,
        )
        store.add(chunks, vectors)
        indexed = wait_until_indexed(store, expect_graph=graph)
        r10, r30, same = [], [], 0
        for vec, on in queries:
            approx = [c for c, _ in store.search(vec, 30, on)]
            exact = [c for c, _ in store.search(vec, 30, on, exact=True)]
            local = [c for c, _ in embedded.search(vec, 30, on)]
            r10.append(overlap(approx, exact, 10))
            r30.append(overlap(approx, exact, 30))
            same += local[:10] == exact[:10]
        out.append(CorpusResult(
            f"{label}: {arm}", len(chunks), indexed, len(queries),
            statistics.fmean(r10), statistics.fmean(r30), min(r10), same,
        ))
        store.client.delete_collection(store.collection)
    return out


# ---------------------------------------------------------------------------
# Part B
# ---------------------------------------------------------------------------

SELECTIVITIES = (1.0, 0.5, 0.1, 0.01, 0.001)
_BASE = date(2024, 8, 1)
_SPAN = 100_000  # days; validity end is spread uniformly over this span


@dataclass
class SweepResult:
    config: str
    selectivity: float
    admitted: int
    recall_at_10: float
    min_recall_at_10: float
    short_results: int  # queries that got fewer than 10 hits although >=10 were eligible


def _sweep_chunks(n: int, rng: np.random.Generator) -> list[SubChunk]:
    """Every point is valid from _BASE; its validity ends at a uniformly random
    offset, so a query on _BASE + t admits a fraction ~(1 - t/_SPAN)."""
    ends = rng.integers(1, _SPAN + 1, size=n)
    return [
        SubChunk(
            chunk_id=f"p{i}", versioned_provision_id=f"p{i}", logical_provision_id=f"p{i}",
            ordinal=0, body="", index_text="", text_valid_from=_BASE,
            text_valid_to=_BASE + timedelta(days=int(e)),
        )
        for i, e in enumerate(ends)
    ]


def bench_selectivity(url: str, n: int = 20_000, dim: int = 768, n_queries: int = 100,
                      seed: int = 0) -> list[SweepResult]:
    rng = np.random.default_rng(seed)
    chunks = _sweep_chunks(n, rng)
    vecs = rng.standard_normal((n, dim)).astype(np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    qs = rng.standard_normal((n_queries, dim)).astype(np.float32)
    qs /= np.linalg.norm(qs, axis=1, keepdims=True)
    on_for = {f: _BASE + timedelta(days=round((1 - f) * _SPAN)) for f in SELECTIVITIES}

    configs = (
        ("payload index, default planner", True, models.HnswConfigDiff(m=16, ef_construct=100)),
        ("payload index, HNSW forced", True, FORCE_HNSW),
        ("no payload index, default planner", False, models.HnswConfigDiff(m=16, ef_construct=100)),
        ("no payload index, HNSW forced", False, FORCE_HNSW),
    )
    results = []
    for name, index_payload, hnsw in configs:
        store = QdrantVectorStore(
            dim, url=url, collection="bench_sweep", hnsw_config=hnsw,
            optimizers_config=FORCE_INDEXING, index_payload=index_payload,
        )
        for i in range(0, n, 1000):
            store.add(chunks[i:i + 1000], vecs[i:i + 1000])
        wait_until_indexed(store, expect_graph=True, timeout=900)
        for f in SELECTIVITIES:
            on = on_for[f]
            admitted = sum(c.valid_on(on) for c in chunks)
            r10, short = [], 0
            for q in qs:
                approx = [c for c, _ in store.search(q, 10, on)]
                exact = [c for c, _ in store.search(q, 10, on, exact=True)]
                r10.append(overlap(approx, exact, 10))
                short += len(approx) < min(10, admitted)
            results.append(SweepResult(name, f, admitted, statistics.fmean(r10), min(r10), short))
        store.client.delete_collection(store.collection)
    return results


def render(corpus: Sequence[CorpusResult], sweep: Sequence[SweepResult]) -> str:
    lines = ["SYSTEM_SPEC.md §21 -- physical filtered search on a Qdrant server", "=" * 96, ""]
    lines.append("A. Real corpus, eval queries on their own relevant_date (HNSW vs exact, same server)")
    lines.append(f"  {'arm':<52}{'points':>7}{'indexed':>9}{'R@10':>8}{'R@30':>8}{'minR@10':>9}{'emb==exact':>12}")
    for r in corpus:
        lines.append(
            f"  {r.label:<52}{r.points:>7}{r.indexed_vectors:>9}{r.recall_at_10:>8.3f}"
            f"{r.recall_at_30:>8.3f}{r.min_recall_at_10:>9.2f}{r.embedded_matches_exact:>7}/{r.queries}"
        )
    lines += ["", "B. Selectivity sweep: 20k seeded random unit vectors, same eligibility_filter"]
    lines.append(f"  {'config':<36}{'admits':>9}{'n':>8}{'R@10':>8}{'minR@10':>9}{'short':>7}")
    for r in sweep:
        lines.append(
            f"  {r.config:<36}{r.selectivity:>8.1%}{r.admitted:>8}{r.recall_at_10:>8.3f}"
            f"{r.min_recall_at_10:>9.2f}{r.short_results:>7}"
        )
    return "\n".join(lines)

"""Hybrid retrieval with hard temporal pre-filtering and parent resolution
(SYSTEM_SPEC.md §7.2, §13.2, §17).

    relevant_date -> eligible chunks (text_valid_from <= d < text_valid_to)
        -> semantic top-N  +  BM25 top-N      (both over eligible chunks only)
        -> reciprocal rank fusion
        -> cross-encoder rerank of the fused head (optional, off by default)
        -> exact-citation priority (explicit provision-level citations only)
        -> parent resolution: chunk -> its full canonical provision, deduplicated
        -> top-k provisions

The comparison arms the evaluation needs are all switches on one pipeline, so a
measured difference can only come from the switch being tested:

  * `mode`: vector-only, BM25-only or hybrid.
  * `rerank`: with or without the cross-encoder. Off by default: on the first
    65-query run (data/eval/results/baseline_v1_65q.txt) bge-reranker-base
    lowered hybrid use-case Recall@5 from 0.946 to 0.875. It stays an eval arm.
  * `citation_priority`: on by default (SYSTEM_SPEC.md §7.4). When the query
    names a provision below article/annex level ("Article 26(7)", "Annex III
    point 4(a)"), eligible chunks whose own citation matches are moved to the top,
    ahead of the fused ranking. It was introduced as an experimental arm whose
    matching rule was fixed before its first run (see `_query_citations`), then
    adopted on the measured result. Off, it is the plain-RRF comparison arm.
  * `temporal`: `pre_filter` is the design. `post_filter` is the rejected
    alternative from §13.2 (rank across every version, drop invalid ones from the
    top-k afterwards) and exists only so candidate starvation can be measured
    rather than asserted. `none` skips the date check entirely, to show what an
    unfiltered index returns.

**Why reciprocal rank fusion.** BM25 scores are unbounded and corpus-dependent;
cosine similarities sit in a narrow band. Adding them requires a calibration that
would drift with every corpus change. RRF uses ranks only: score = sum of
1 / (rrf_k + rank). The constant 60 is the value from the original RRF paper
(Cormack et al., 2009) and is not tuned here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum

import numpy as np

from ..corpus.models import Provision
from .bm25 import BM25Index, tokenize
from .chunking import DEFAULT_MAX_CHARS, SubChunk, chunk_corpus, short_citation
from .models import DedupEmbeddingCache, Encoder, Reranker
from .vector_store import QdrantVectorStore


class Mode(StrEnum):
    VECTOR = "vector"
    BM25 = "bm25"
    HYBRID = "hybrid"


class TemporalMode(StrEnum):
    PRE_FILTER = "pre_filter"
    POST_FILTER = "post_filter"
    NONE = "none"


@dataclass(frozen=True)
class RetrievalConfig:
    mode: Mode = Mode.HYBRID
    temporal: TemporalMode = TemporalMode.PRE_FILTER
    rerank: bool = False
    citation_priority: bool = True
    #: Chunks each retriever contributes to fusion.
    candidate_depth: int = 30
    #: Fused chunks the cross-encoder rescores.
    rerank_depth: int = 20
    rrf_k: int = 60

    @property
    def label(self) -> str:
        cite = "+cite" if self.citation_priority else ""
        rerank = "+rerank" if self.rerank else ""
        return f"{self.mode.value}{cite}{rerank}/{self.temporal.value}"


@dataclass(frozen=True)
class RetrievedProvision:
    """A retrieval result: always a complete canonical provision (§7.2), plus
    which of its chunks matched -- kept for explanation, never for generation."""

    provision: Provision
    rank: int
    score: float
    matched_chunk_ids: tuple[str, ...] = field(default=())


def _citation_tokens(text: str) -> set[str]:
    return {t for t in tokenize(text) if t.startswith(("art_", "annex_"))}


def _query_citations(query: str) -> set[str]:
    """The most specific sub-article citations a query names.

    Rule (fixed before the experimental arm was first run):
      * only citations *below* article/annex level count -- "art_26_7",
        "annex_iii_4" -- because a bare "Article 6" or "Annex III" names dozens of
        provisions and would promote all of them;
      * of nested citations, only the deepest counts: "Article 5(1)(h)" yields
        art_5_1_h, not also art_5_1.
    A query citing a provision outside the corpus ("Article 49(2)") simply
    matches nothing. See `_cites` for how a query citation matches a chunk.
    """
    specific = {t for t in _citation_tokens(query) if t.count("_") >= 2}
    return {t for t in specific if not any(u.startswith(t + "_") for u in specific)}


def _cites(wanted: set[str], chunk_tokens: set[str]) -> bool:
    """A query citation matches a chunk when it names the chunk's provision or
    an ancestor of it ("Article 25(3)" -> 25(3)(a) and 25(3)(b)), or a unit
    *inside* it: "Article 5(1)(h)(iii)" -> 5(1)(h), since (iii) is not its own
    canonical provision. Only the chunk's deepest citation is used for the
    inside case, or "Article 5(1)(h)" would also match 5(1)(a) via art_5_1."""
    if wanted & chunk_tokens:
        return True
    deepest = max(chunk_tokens, key=lambda t: t.count("_"), default=None)
    return deepest is not None and any(q.startswith(deepest + "_") for q in wanted)


def reciprocal_rank_fusion(rankings: Sequence[Sequence[str]], k: int = 60) -> list[tuple[str, float]]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda x: -x[1])


def resolve_parents(
    ranked_chunks: Sequence[tuple[str, float]],
    chunks: dict[str, SubChunk],
    provisions: dict[str, Provision],
) -> list[RetrievedProvision]:
    """Map ranked chunks to their full parent provisions (§7.2 invariant).

    A provision ranks at its best-ranked chunk and appears once, however many of
    its chunks matched. The returned object is the canonical `Provision` itself,
    so there is no code path by which a bare sub-chunk reaches a caller.
    """
    order: list[str] = []
    best: dict[str, float] = {}
    matched: dict[str, list[str]] = {}
    for chunk_id, score in ranked_chunks:
        vid = chunks[chunk_id].versioned_provision_id
        if vid not in best:
            order.append(vid)
            best[vid] = score
            matched[vid] = []
        matched[vid].append(chunk_id)
    return [
        RetrievedProvision(
            provision=provisions[vid],
            rank=i,
            score=best[vid],
            matched_chunk_ids=tuple(matched[vid]),
        )
        for i, vid in enumerate(order, start=1)
    ]


class Retriever:
    def __init__(
        self,
        provisions: Sequence[Provision],
        encoder: Encoder,
        reranker: Reranker | None = None,
        *,
        max_chars: int = DEFAULT_MAX_CHARS,
        qdrant_url: str | None = None,
        embeddings: DedupEmbeddingCache | None = None,
    ):
        self.provisions = {p.versioned_provision_id: p for p in provisions}
        self.chunk_list = chunk_corpus(list(provisions), max_chars)
        self.chunks = {c.chunk_id: c for c in self.chunk_list}
        self.encoder = encoder
        self.reranker = reranker
        # Pass a shared cache to build several indexes (e.g. the real and the
        # synthetic-version corpus) while computing each distinct text once.
        self.embeddings = embeddings or DedupEmbeddingCache(encoder)
        self.store = QdrantVectorStore(encoder.dimension, url=qdrant_url)
        vectors = self.embeddings.embed([(c.content_hash, c.index_text) for c in self.chunk_list])
        self.store.add(self.chunk_list, vectors)
        self._bm25_cache: dict[tuple[str, ...], tuple[BM25Index, list[SubChunk]]] = {}
        self._query_vectors: dict[str, np.ndarray] = {}
        self._rerank_scores: dict[tuple[str, str], float] = {}
        self._chunk_citations = {
            c.chunk_id: _citation_tokens(short_citation(self.provisions[c.versioned_provision_id]))
            for c in self.chunk_list
        }

    # -- candidate generation -------------------------------------------------

    def _bm25_for(self, eligible: list[SubChunk]) -> BM25Index:
        """BM25 built over exactly the eligible chunks, so an ineligible version
        cannot shape IDF either. Cached per eligible set: many dates share one."""
        key = tuple(c.chunk_id for c in eligible)
        if key not in self._bm25_cache:
            self._bm25_cache[key] = (BM25Index([c.index_text for c in eligible]), eligible)
        return self._bm25_cache[key][0]

    def _query_vector(self, query: str) -> np.ndarray:
        if query not in self._query_vectors:
            self._query_vectors[query] = np.asarray(self.encoder.encode_query(query))
        return self._query_vectors[query]

    def _candidates(self, query: str, eligible_on: date | None, config: RetrievalConfig) -> list[tuple[str, float]]:
        eligible = [c for c in self.chunk_list if eligible_on is None or c.valid_on(eligible_on)]
        if not eligible:
            return []
        rankings: list[list[tuple[str, float]]] = []
        if config.mode in (Mode.VECTOR, Mode.HYBRID):
            rankings.append(
                self.store.search(self._query_vector(query), config.candidate_depth, eligible_on)
            )
        if config.mode in (Mode.BM25, Mode.HYBRID):
            bm25 = self._bm25_for(eligible)
            rankings.append(
                [(eligible[i].chunk_id, s) for i, s in bm25.top(query, config.candidate_depth)]
            )
        if len(rankings) == 1:
            return rankings[0]
        return reciprocal_rank_fusion(
            [[cid for cid, _ in r] for r in rankings], config.rrf_k
        )

    def _rerank(self, query: str, head: list[tuple[str, float]]) -> list[tuple[str, float]]:
        """Cross-encoder rescoring, memoised per (query, chunk): the eval asks the
        same pairs repeatedly across k values and arms, and a cross-encoder pass
        is the most expensive step in the pipeline."""
        todo = [cid for cid, _ in head if (query, cid) not in self._rerank_scores]
        if todo:
            scores = self.reranker.score(query, [self.chunks[cid].index_text for cid in todo])
            self._rerank_scores.update({(query, cid): s for cid, s in zip(todo, scores, strict=True)})
        return sorted(
            ((cid, self._rerank_scores[(query, cid)]) for cid, _ in head), key=lambda x: -x[1]
        )

    def _prioritise_citations(
        self, query: str, ranked: list[tuple[str, float]], eligible_on: date | None
    ) -> list[tuple[str, float]]:
        wanted = _query_citations(query)
        if not wanted:
            return ranked
        hit = {
            c.chunk_id for c in self.chunk_list
            if (eligible_on is None or c.valid_on(eligible_on))
            and _cites(wanted, self._chunk_citations[c.chunk_id])
        }
        if not hit:
            return ranked
        in_ranking = [(cid, s) for cid, s in ranked if cid in hit]
        seen = {cid for cid, _ in in_ranking}
        # An exact citation is a lookup, not a relevance guess: a cited chunk the
        # fused ranking never surfaced is still promoted.
        missing = [(c.chunk_id, 0.0) for c in self.chunk_list if c.chunk_id in hit and c.chunk_id not in seen]
        return in_ranking + missing + [(cid, s) for cid, s in ranked if cid not in hit]

    # -- public API -------------------------------------------------------------

    def retrieve(
        self,
        query: str,
        relevant_date: date,
        k: int = 5,
        config: RetrievalConfig = RetrievalConfig(),
    ) -> list[RetrievedProvision]:
        pre = config.temporal == TemporalMode.PRE_FILTER
        ranked = self._candidates(query, relevant_date if pre else None, config)

        if config.rerank and self.reranker is not None and ranked:
            ranked = self._rerank(query, ranked[: config.rerank_depth])

        if config.citation_priority:
            ranked = self._prioritise_citations(query, ranked, relevant_date if pre else None)

        results = resolve_parents(ranked, self.chunks, self.provisions)[:k]

        if config.temporal == TemporalMode.POST_FILTER:
            # The rejected alternative, reproduced faithfully: the top-k was cut
            # across *all* versions first, so whatever the date check removes is
            # simply gone -- nothing eligible is promoted to take its place.
            results = [r for r in results if r.provision.text_valid_on(relevant_date)]
        return results

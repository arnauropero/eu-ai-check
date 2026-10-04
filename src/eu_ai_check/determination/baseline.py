"""Baseline arm B: a retrieval-score cutoff (SYSTEM_SPEC.md §4.2, §18).

Score = the maximum cosine similarity (bge-base-en-v1.5, the Phase 1 embedder)
between the description and any chunk of a candidate. Per scope, the top
candidate `matches` if its score is >= tau; every other candidate
`does_not_match`. It can never say `insufficient`, so it can never produce
`unresolved` from matching. Showing that limit is part of the comparison.

tau is fitted per scope by maximising Youden's J on the Phase 1 retrieval
use-case queries, before any determination run. Scopes with no positive query
use one tau pooled across all scopes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np

from ..corpus.models import Provision
from ..retrieval.chunking import chunk_provision
from ..retrieval.models import Encoder
from .matcher import DOES_NOT_MATCH, MATCHES, MatchResult, Verdict
from .scopes import SCOPES


class _ChunkVectors:
    """Chunk vectors per provision, encoded once and reused across cases."""

    def __init__(self, encoder: Encoder):
        self.encoder = encoder
        self._cache: dict[str, np.ndarray] = {}

    def of(self, provision: Provision) -> np.ndarray:
        key = provision.versioned_provision_id
        if key not in self._cache:
            texts = [c.index_text for c in chunk_provision(provision)]
            self._cache[key] = np.asarray(self.encoder.encode_passages(texts))
        return self._cache[key]

    def score(self, query_vector: np.ndarray, provision: Provision) -> float:
        return float(np.max(self.of(provision) @ query_vector))


class CutoffBaseline:
    name = "baseline"

    def __init__(self, encoder: Encoder, tau: dict[str, float]):
        self.vectors = _ChunkVectors(encoder)
        self.tau = tau

    def match(self, description: str, candidates: dict[str, list[Provision]]) -> MatchResult:
        query = np.asarray(self.vectors.encoder.encode_query(description))
        verdicts: dict[str, Verdict] = {}
        for scope, provisions in candidates.items():
            if not provisions:
                continue
            scored = sorted(((self.vectors.score(query, p), p.logical_provision_id)
                             for p in provisions), reverse=True)
            top_score, top_id = scored[0]
            for score, pid in scored:
                hit = pid == top_id and top_score >= self.tau[scope]
                verdicts[pid] = Verdict(pid, MATCHES if hit else DOES_NOT_MATCH,
                                        rationale=f"cosine {score:.4f} vs tau "
                                                  f"{self.tau[scope]:.4f}")
        return MatchResult(verdicts)


def youden_threshold(scores: Sequence[float], labels: Sequence[bool]) -> float:
    """The threshold maximising TPR - FPR over the observed scores. Ties go to the
    highest threshold, the more conservative cut."""
    positives = sum(labels)
    negatives = len(labels) - positives
    if positives == 0 or negatives == 0:
        raise ValueError("Youden's J needs at least one positive and one negative")
    best: tuple[float, float] | None = None
    for t in sorted(set(scores), reverse=True):
        tpr = sum(1 for s, y in zip(scores, labels) if y and s >= t) / positives
        fpr = sum(1 for s, y in zip(scores, labels) if not y and s >= t) / negatives
        j = tpr - fpr
        if best is None or j > best[0]:
            best = (j, t)
    return best[1]


@dataclass(frozen=True)
class TauFit:
    tau: dict[str, float]
    detail: dict[str, dict]

    def to_json(self) -> dict:
        return {"tau": self.tau, "detail": self.detail}


def fit_tau(encoder: Encoder, provisions: list[Provision],
            queries: list[dict]) -> TauFit:
    """Fit tau per scope on the Phase 1 use-case queries (pre-registered §18)."""
    vectors = _ChunkVectors(encoder)
    use_case = [q for q in queries if q["category"] == "use_case"]
    pooled_scores: list[float] = []
    pooled_labels: list[bool] = []
    per_scope: dict[str, tuple[list[float], list[bool]]] = {}
    for scope in SCOPES:
        members = [p for p in provisions if scope.contains(p.logical_provision_id)]
        scores, labels = [], []
        for q in use_case:
            qv = np.asarray(encoder.encode_query(q["query"]))
            scores.append(max(vectors.score(qv, p) for p in members))
            labels.append(any(scope.contains(pid) for pid in q["relevant"]))
        per_scope[scope.name] = (scores, labels)
        pooled_scores += scores
        pooled_labels += labels

    pooled = youden_threshold(pooled_scores, pooled_labels)
    tau: dict[str, float] = {}
    detail: dict[str, dict] = {}
    for name, (scores, labels) in per_scope.items():
        positives = sum(labels)
        if positives:
            tau[name] = youden_threshold(scores, labels)
            source = "fitted"
        else:
            tau[name] = pooled
            source = "pooled (no positive query)"
        detail[name] = {"positives": positives, "negatives": len(labels) - positives,
                        "source": source}
    detail["_pooled"] = {"tau": pooled, "pairs": len(pooled_scores),
                         "positives": sum(pooled_labels)}
    return TauFit(tau, detail)


def load_tau(path: Path) -> dict[str, float]:
    return json.loads(path.read_text(encoding="utf-8"))["tau"]

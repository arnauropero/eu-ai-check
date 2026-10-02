"""Model wrappers: embedding (with compute-time dedup) and cross-encoder reranking.

Both sit behind small protocols so tests can substitute deterministic fakes and
never download a model; the real implementations load lazily.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

import numpy as np

EMBEDDING_MODEL = "BAAI/bge-base-en-v1.5"
RERANKER_MODEL = "BAAI/bge-reranker-base"

#: bge v1.5's recommended query-side instruction for short-query-to-passage
#: retrieval. Passages are embedded without it.
_BGE_QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "


class Encoder(Protocol):
    dimension: int

    def encode_passages(self, texts: Sequence[str]) -> np.ndarray: ...
    def encode_query(self, text: str) -> np.ndarray: ...


class Reranker(Protocol):
    def score(self, query: str, passages: Sequence[str]) -> list[float]: ...


class SentenceTransformerEncoder:
    """bge-base-en-v1.5; vectors are L2-normalised so dot product is cosine."""

    dimension = 768

    def __init__(self, model_name: str = EMBEDDING_MODEL):
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_name)

    def encode_passages(self, texts: Sequence[str]) -> np.ndarray:
        return self._model.encode(
            list(texts), normalize_embeddings=True, batch_size=16, show_progress_bar=False
        )

    def encode_query(self, text: str) -> np.ndarray:
        return self._model.encode(
            _BGE_QUERY_INSTRUCTION + text, normalize_embeddings=True, show_progress_bar=False
        )


class CrossEncoderReranker:
    """bge-reranker-base: scores (query, passage) jointly, which a bi-encoder
    cannot -- it sees which query terms the passage actually answers."""

    def __init__(self, model_name: str = RERANKER_MODEL):
        from sentence_transformers import CrossEncoder

        self._model = CrossEncoder(model_name)

    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        if not passages:
            return []
        return [float(s) for s in self._model.predict([(query, p) for p in passages])]


class DedupEmbeddingCache:
    """Compute-time embedding dedup, keyed by content hash (SYSTEM_SPEC.md §15).

    The encoder is called once per distinct content hash, however many index
    documents share that text. This is the *only* dedup the system guarantees:
    the index still holds one searchable document per version, because the
    temporal pre-filter needs version-level metadata to filter on.
    """

    def __init__(self, encoder: Encoder):
        self.encoder = encoder
        self._vectors: dict[str, np.ndarray] = {}
        self.passages_encoded = 0

    def embed(self, items: Sequence[tuple[str, str]]) -> list[np.ndarray]:
        """`items` are (content_hash, text) pairs; returns one vector per item."""
        missing: dict[str, str] = {}
        for key, text in items:
            if key not in self._vectors and key not in missing:
                missing[key] = text
        if missing:
            vectors = self.encoder.encode_passages(list(missing.values()))
            for key, vec in zip(missing, vectors, strict=True):
                self._vectors[key] = np.asarray(vec, dtype=np.float32)
            self.passages_encoded += len(missing)
        return [self._vectors[key] for key, _ in items]

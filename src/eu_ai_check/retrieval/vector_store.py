"""Qdrant vector index over sub-chunks, with date-eligibility as a payload filter.

Defaults to Qdrant's embedded local mode (`location=":memory:"`), so the project
runs with no server. Pass `url=` to run the identical code against a Qdrant
server.

**What local mode does and does not prove.** Local mode answers queries by exact
scan, not HNSW. It proves the filter is *logically* right -- the payload filter
admits exactly the versions valid on `relevant_date`. It cannot answer
SYSTEM_SPEC.md §21's open question about *physical* filtered-HNSW recall under a
selective filter. That needs a server-mode run, which the `url=` path supports.

Validity is stored as day ordinals, because a numeric range filter is the
portable way to express `text_valid_from <= d < text_valid_to`. An open-ended
version (`text_valid_to = None`) is stored with a sentinel far in the future.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date

import numpy as np
from qdrant_client import QdrantClient, models

from .chunking import SubChunk

COLLECTION = "provision_chunks"
_OPEN_ENDED = date.max.toordinal()
_ID_NAMESPACE = uuid.UUID("6f1c9a52-6f8e-4d2a-9a1e-2b9f0c7d4e11")


def _point_id(chunk_id: str) -> str:
    return str(uuid.uuid5(_ID_NAMESPACE, chunk_id))


def eligibility_filter(on: date) -> models.Filter:
    """text_valid_from <= on < text_valid_to -- the §13.2 hard constraint."""
    d = on.toordinal()
    return models.Filter(
        must=[
            models.FieldCondition(key="valid_from", range=models.Range(lte=d)),
            models.FieldCondition(key="valid_to", range=models.Range(gt=d)),
        ]
    )


class QdrantVectorStore:
    """`hnsw_config` / `optimizers_config` / `index_payload` exist for the §21
    filtered-HNSW benchmark (`filter_bench.py`); the defaults are what the
    retriever uses. The payload indexes are created *before* any points are
    added: Qdrant builds its filter-aware extra HNSW links only for fields that
    are indexed when the graph is built."""

    def __init__(
        self,
        dimension: int,
        *,
        location: str = ":memory:",
        url: str | None = None,
        collection: str = COLLECTION,
        hnsw_config: models.HnswConfigDiff | None = None,
        optimizers_config: models.OptimizersConfigDiff | None = None,
        index_payload: bool = True,
    ):
        self.client = QdrantClient(url=url) if url else QdrantClient(location=location)
        self.collection = collection
        if self.client.collection_exists(collection):
            self.client.delete_collection(collection)
        self.client.create_collection(
            collection,
            vectors_config=models.VectorParams(size=dimension, distance=models.Distance.COSINE),
            hnsw_config=hnsw_config,
            optimizers_config=optimizers_config,
        )
        if index_payload:
            for key in ("valid_from", "valid_to"):
                self.client.create_payload_index(collection, key, models.PayloadSchemaType.INTEGER)

    def add(self, chunks: Sequence[SubChunk], vectors: Sequence[np.ndarray]) -> None:
        self.client.upsert(
            self.collection,
            wait=True,
            points=[
                models.PointStruct(
                    id=_point_id(c.chunk_id),
                    vector=v.tolist(),
                    payload={
                        "chunk_id": c.chunk_id,
                        "valid_from": c.text_valid_from.toordinal(),
                        "valid_to": (
                            c.text_valid_to.toordinal() if c.text_valid_to else _OPEN_ENDED
                        ),
                    },
                )
                for c, v in zip(chunks, vectors, strict=True)
            ],
        )

    def search(
        self, vector: np.ndarray, limit: int, eligible_on: date | None, *, exact: bool = False
    ) -> list[tuple[str, float]]:
        """(chunk_id, cosine) pairs. `eligible_on=None` searches every version --
        used only to measure the rejected post-filter alternative. `exact=True`
        forces a brute-force scan on a server, as ground truth for the HNSW path."""
        hits = self.client.query_points(
            self.collection,
            query=vector.tolist(),
            query_filter=eligibility_filter(eligible_on) if eligible_on else None,
            limit=limit,
            with_payload=True,
            search_params=models.SearchParams(exact=exact),
        ).points
        return [(h.payload["chunk_id"], h.score) for h in hits]

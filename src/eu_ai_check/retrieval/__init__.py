"""Phase 1 retrieval layer (SYSTEM_SPEC.md §7, §13, §15, §17).

    provisions -> sub-chunks -> {Qdrant vector index, BM25 index}
    query + relevant_date -> temporal eligibility (hard, pre-retrieval)
        -> semantic + BM25 -> RRF fusion -> optional cross-encoder rerank
        -> parent resolution -> full canonical provisions
"""

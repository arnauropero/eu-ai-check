"""Phase 1 retrieval tests (PROJECT_PLAN.md §6, Phase 1; SYSTEM_SPEC.md §7, §13, §15).

These test the retrieval *invariants* -- parent resolution, the temporal hard
filter, compute-time embedding dedup -- against the real ingested corpus. They use
a deterministic hashing encoder instead of bge, so they run in seconds and never
download a model. Ranking *quality* is not asserted here: that is what the eval
harness measures (`eu-ai-check eval-retrieval`), and a quality number pinned in a
unit test would only be a fake-encoder number.
"""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

import numpy as np
import pytest

from eu_ai_check.corpus.models import Corpus, Provision
from eu_ai_check.retrieval.bm25 import BM25Index, tokenize
from eu_ai_check.retrieval.chunking import chunk_corpus, chunk_provision, short_citation
from eu_ai_check.retrieval.evaluation import (
    apply_synthetic_versions,
    expected_versions,
    load_queries,
)
from eu_ai_check.retrieval.models import DedupEmbeddingCache
from eu_ai_check.retrieval.retriever import (
    Mode,
    RetrievalConfig,
    Retriever,
    TemporalMode,
    reciprocal_rank_fusion,
)

ROOT = Path(__file__).resolve().parents[1]
CORPUS_DIR = ROOT / "data/corpus"
QUERIES = ROOT / "data/eval/retrieval_queries.json"
SYNTHETIC = ROOT / "data/eval/synthetic_versions.json"

BEFORE_AMENDMENT = date(2025, 1, 15)
AFTER_AMENDMENT = date(2026, 1, 15)
BEFORE_ENTRY_INTO_FORCE = date(2024, 6, 1)


class HashingEncoder:
    """Deterministic bag-of-tokens encoder: a stand-in for bge with the same
    interface, so tests exercise the real Qdrant path without a model."""

    dimension = 256

    def __init__(self) -> None:
        self.calls: list[int] = []

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(self.dimension, dtype=np.float32)
        for tok in tokenize(text):
            v[int(hashlib.md5(tok.encode()).hexdigest(), 16) % self.dimension] += 1.0
        n = np.linalg.norm(v)
        return v / n if n else v

    def encode_passages(self, texts):
        self.calls.append(len(texts))
        return np.stack([self._vec(t) for t in texts])

    def encode_query(self, text):
        return self._vec(text)


BM25_ONLY = RetrievalConfig(mode=Mode.BM25, rerank=False)


@pytest.fixture(scope="session")
def corpus() -> Corpus:
    return Corpus.load(CORPUS_DIR)


@pytest.fixture(scope="session")
def versioned(corpus: Corpus) -> list[Provision]:
    return apply_synthetic_versions(corpus.provisions, SYNTHETIC)


@pytest.fixture(scope="session")
def retriever(corpus: Corpus) -> Retriever:
    return Retriever(corpus.provisions, HashingEncoder())


@pytest.fixture(scope="session")
def versioned_retriever(versioned: list[Provision]) -> Retriever:
    return Retriever(versioned, HashingEncoder())


def find(provisions, logical_id: str) -> Provision:
    (p,) = [p for p in provisions if p.logical_provision_id == logical_id]
    return p


# --------------------------------------------------------------------------
# Sub-chunking (§7.2)
# --------------------------------------------------------------------------

def test_chunking_is_lossless(corpus: Corpus) -> None:
    """Splitting never drops or rewrites legal text: a provision's chunks rejoin
    to exactly its canonical text."""
    for p in corpus.provisions:
        chunks = chunk_provision(p)
        assert " ".join(c.body for c in chunks) == p.text, p.logical_provision_id


def test_only_long_provisions_are_split(corpus: Corpus) -> None:
    for p in corpus.provisions:
        n = len(chunk_provision(p, max_chars=800))
        assert (n > 1) == (len(p.text) > 800), p.logical_provision_id


def test_longest_provision_is_article_26_10_and_is_split(corpus: Corpus) -> None:
    """Corrects a Phase 0 note that named Article 5(1)(h) as the longest
    provision. It is 26(10), and bge-base's 512-token window would truncate it if
    it were embedded whole."""
    longest = max(corpus.provisions, key=lambda p: len(p.text))
    assert longest.logical_provision_id == "aiact:art_26:para_10"
    assert len(chunk_provision(longest)) > 1


def test_chunk_header_carries_short_citation(corpus: Corpus) -> None:
    assert short_citation(find(corpus.provisions, "aiact:art_5:para_1:point_h")) == "Article 5(1)(h)"
    assert short_citation(find(corpus.provisions, "aiact:anx_III:area_4:item_a")) == "Annex III point 4(a)"
    assert short_citation(find(corpus.provisions, "aiact:anx_III:area_2")) == "Annex III point 2"
    chunk = chunk_provision(find(corpus.provisions, "aiact:art_26:para_7"))[0]
    assert chunk.index_text.startswith("Article 26(7) - Obligations of deployers")


# --------------------------------------------------------------------------
# Parent resolution (§7.1 / §7.2 invariant, Article 5(1)(h))
# --------------------------------------------------------------------------

def test_article_5_1_h_has_a_chunk_without_its_prohibition(corpus: Corpus) -> None:
    """The precondition that makes parent resolution load-bearing: one chunk of
    5(1)(h) states exception (iii) without the prohibition it excepts from."""
    chunks = chunk_provision(find(corpus.provisions, "aiact:art_5:para_1:point_h"))
    orphan = [c for c in chunks if "(iii)" in c.body]
    assert orphan and "shall be prohibited" not in orphan[0].body


def test_match_on_exception_iii_returns_full_article_5_1_h(retriever: Retriever) -> None:
    hits = retriever.retrieve(
        "localisation or identification of a person suspected of having committed a "
        "criminal offence punishable by a custodial sentence of at least four years",
        date(2026, 9, 1),
        k=3,
        config=BM25_ONLY,
    )
    top = hits[0]
    assert top.provision.logical_provision_id == "aiact:art_5:para_1:point_h"
    assert top.matched_chunk_ids[0].endswith("#c1")  # matched on the (iii) chunk ...
    assert "The following AI practices shall be prohibited" in top.provision.text  # ... got the whole
    assert "(i) the targeted search" in top.provision.text


def test_results_are_whole_provisions_never_chunks(retriever: Retriever, corpus: Corpus) -> None:
    by_vid = {p.versioned_provision_id: p for p in corpus.provisions}
    for mode in Mode:
        for h in retriever.retrieve("deployer post-remote biometric identification", date(2026, 9, 1), k=10,
                                    config=RetrievalConfig(mode=mode, rerank=False)):
            assert h.provision is by_vid[h.provision.versioned_provision_id]


def test_a_provision_appears_once_however_many_chunks_match(retriever: Retriever) -> None:
    hits = retriever.retrieve(
        "post-remote biometric identification authorisation deployer annual reports",
        date(2026, 9, 1), k=10, config=BM25_ONLY,
    )
    vids = [h.provision.versioned_provision_id for h in hits]
    assert len(vids) == len(set(vids))
    art_26_10 = next(h for h in hits if h.provision.logical_provision_id == "aiact:art_26:para_10")
    assert len(art_26_10.matched_chunk_ids) > 1


# --------------------------------------------------------------------------
# Temporal hard filter (§13.2)
# --------------------------------------------------------------------------

def test_synthetic_fixture_keeps_version_integrity(versioned: list[Provision], corpus: Corpus) -> None:
    assert len(versioned) == len(corpus.provisions) + 8
    old, new = sorted(
        (p for p in versioned if p.logical_provision_id == "aiact:art_26:para_6"),
        key=lambda p: p.text_valid_from,
    )
    assert old.text_valid_to == new.text_valid_from == date(2025, 6, 1)
    assert old.content_id != new.content_id
    assert "twelve months" in new.text and "six months" in old.text


@pytest.mark.parametrize("mode", list(Mode))
@pytest.mark.parametrize("on", [BEFORE_AMENDMENT, AFTER_AMENDMENT])
def test_pre_filter_never_returns_an_ineligible_version(
    versioned_retriever: Retriever, mode: Mode, on: date
) -> None:
    for query in ("keep logs for at least twelve months", "physical or digital CE marking",
                  "emotion recognition in recruitment procedures"):
        hits = versioned_retriever.retrieve(query, on, k=10, config=RetrievalConfig(mode=mode, rerank=False))
        assert hits
        assert all(h.provision.text_valid_on(on) for h in hits)


def test_same_query_two_dates_two_versions(versioned_retriever: Retriever) -> None:
    query = "deployers keep logs for at least twelve months"
    before = versioned_retriever.retrieve(query, BEFORE_AMENDMENT, k=1, config=BM25_ONLY)[0]
    after = versioned_retriever.retrieve(query, AFTER_AMENDMENT, k=1, config=BM25_ONLY)[0]
    assert before.provision.logical_provision_id == after.provision.logical_provision_id
    assert before.provision.versioned_provision_id == "aiact:art_26:para_6@2024-08-01"
    assert after.provision.versioned_provision_id == "aiact:art_26:para_6@2025-06-01"


def test_unfiltered_retrieval_cites_a_version_that_did_not_exist_yet(versioned_retriever: Retriever) -> None:
    """What the hard filter prevents: with no filter, the amended wording wins and
    the system would cite 2025 text for a 2025-01-15 scenario."""
    top = versioned_retriever.retrieve(
        "deployers keep logs for at least twelve months", BEFORE_AMENDMENT, k=1,
        config=RetrievalConfig(mode=Mode.BM25, rerank=False, temporal=TemporalMode.NONE),
    )[0]
    assert not top.provision.text_valid_on(BEFORE_AMENDMENT)


def test_post_filter_starves_where_pre_filter_does_not(versioned_retriever: Retriever) -> None:
    """The rejected alternative (§13.2): cut top-k across all versions, then drop
    invalid ones. The ineligible new version occupies a top-k slot and the
    eligible one never gets in."""
    query = "deployers keep logs for at least twelve months"
    post = versioned_retriever.retrieve(
        query, BEFORE_AMENDMENT, k=1,
        config=RetrievalConfig(mode=Mode.BM25, rerank=False, temporal=TemporalMode.POST_FILTER),
    )
    pre = versioned_retriever.retrieve(query, BEFORE_AMENDMENT, k=1, config=BM25_ONLY)
    assert post == []
    assert pre[0].provision.versioned_provision_id == "aiact:art_26:para_6@2024-08-01"


@pytest.mark.parametrize("mode", list(Mode))
def test_nothing_is_retrieved_before_entry_into_force(retriever: Retriever, mode: Mode) -> None:
    assert retriever.retrieve("prohibited AI practices", BEFORE_ENTRY_INTO_FORCE,
                              config=RetrievalConfig(mode=mode, rerank=False)) == []


def test_validity_window_is_half_open(versioned_retriever: Retriever) -> None:
    """On the amendment date itself, only the new version is valid."""
    on = date(2025, 6, 1)
    hits = versioned_retriever.retrieve("logs at least six months twelve months", on, k=10, config=BM25_ONLY)
    vids = {h.provision.versioned_provision_id for h in hits}
    assert "aiact:art_26:para_6@2025-06-01" in vids
    assert "aiact:art_26:para_6@2024-08-01" not in vids


# --------------------------------------------------------------------------
# Compute-time embedding dedup (§15)
# --------------------------------------------------------------------------

def test_identical_text_is_embedded_once_but_indexed_per_version(corpus: Corpus) -> None:
    p = find(corpus.provisions, "aiact:art_16:point_a")
    # A renumbering-style version: same text, new version identity.
    v2 = p.model_copy(update={
        "versioned_provision_id": f"{p.logical_provision_id}@2025-06-01",
        "text_valid_from": date(2025, 6, 1),
    })
    v1 = p.model_copy(update={"text_valid_to": date(2025, 6, 1)})
    encoder = HashingEncoder()
    r = Retriever([v1, v2], encoder)
    assert r.embeddings.passages_encoded == 1
    assert len(r.chunk_list) == 2  # one searchable document per version
    early = r.retrieve("requirements set out in Section 2", date(2025, 1, 1), config=BM25_ONLY)
    late = r.retrieve("requirements set out in Section 2", date(2026, 1, 1), config=BM25_ONLY)
    assert [h.provision.versioned_provision_id for h in early] == [v1.versioned_provision_id]
    assert [h.provision.versioned_provision_id for h in late] == [v2.versioned_provision_id]


def test_shared_cache_encodes_only_changed_text(corpus: Corpus, versioned: list[Provision]) -> None:
    cache = DedupEmbeddingCache(HashingEncoder())
    real = Retriever(corpus.provisions, cache.encoder, embeddings=cache)
    after_real = cache.passages_encoded
    Retriever(versioned, cache.encoder, embeddings=cache)
    changed = {c.content_hash for c in chunk_corpus(versioned)} - {c.content_hash for c in real.chunk_list}
    assert cache.passages_encoded - after_real == len(changed)


# --------------------------------------------------------------------------
# BM25 and fusion
# --------------------------------------------------------------------------

def test_tokenizer_emits_compound_citation_tokens() -> None:
    toks = tokenize("Article 5(1)(h) and Annex III point 4(a)")
    assert {"art_5", "art_5_1", "art_5_1_h", "annex_iii", "annex_iii_4", "annex_iii_4_a"} <= set(toks)


def test_bm25_resolves_exact_article_reference(retriever: Retriever) -> None:
    top = retriever.retrieve("Article 26(7)", date(2026, 9, 1), k=1, config=BM25_ONLY)[0]
    assert top.provision.logical_provision_id == "aiact:art_26:para_7"


def test_bm25_ignores_documents_with_no_matching_term() -> None:
    idx = BM25Index(["alpha beta", "gamma delta"])
    assert [i for i, _ in idx.top("alpha", 5)] == [0]


def test_reciprocal_rank_fusion_rewards_agreement() -> None:
    fused = reciprocal_rank_fusion([["a", "b", "c"], ["b", "c", "a"]])
    assert fused[0][0] == "b"


# --------------------------------------------------------------------------
# Eval set integrity
# --------------------------------------------------------------------------

def test_every_eval_query_has_resolvable_ground_truth(corpus: Corpus, versioned: list[Provision]) -> None:
    """A ground-truth id that matches no provision would silently score as a
    permanent miss. Every relevant id must resolve to exactly one version on the
    query's date, in the corpus that query is evaluated against."""
    queries = load_queries(QUERIES)
    assert len({q.id for q in queries}) == len(queries)
    for q in queries:
        provisions = versioned if q.category == "temporal" else corpus.provisions
        expected = expected_versions(q, provisions)
        assert len(expected) == len(q.relevant), q.id


def test_temporal_queries_are_paired_across_the_amendment(versioned: list[Provision]) -> None:
    queries = {q.id: q for q in load_queries(QUERIES) if q.category == "temporal" and q.relevant}
    for qid, q in queries.items():
        if qid.endswith("a"):
            twin = queries[qid[:-1] + "b"]
            assert q.query == twin.query
            assert expected_versions(q, versioned) != expected_versions(twin, versioned)


def test_synthetic_fixture_rejects_an_edit_that_does_not_apply(tmp_path: Path, corpus: Corpus) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(
        '{"amendment_date": "2025-06-01", "amendments": [{"logical_provision_id": '
        '"aiact:art_26:para_6", "replace": ["no such words", "x"]}]}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="not in text"):
        apply_synthetic_versions(corpus.provisions, bad)


# --------------------------------------------------------------------------
# Defaults and exact-citation priority
# --------------------------------------------------------------------------

def test_default_design_is_hybrid_with_citation_priority_and_no_reranker() -> None:
    """The adopted Phase 1 design (SYSTEM_SPEC.md §17). Reranker off: it lowered
    hybrid Recall@5 in eval. Citation priority on: it fixed RRF diluting exact
    citation matches, with no change on queries that cite nothing."""
    config = RetrievalConfig()
    assert config.mode == Mode.HYBRID
    assert config.temporal == TemporalMode.PRE_FILTER
    assert config.rerank is False
    assert config.citation_priority is True


@pytest.mark.parametrize("query, expected", [
    ("Article 26(7)", {"art_26_7"}),
    ("Article 5(1)(h)(iii)", {"art_5_1_h_iii"}),
    ("Annex III point 4(a)", {"annex_iii_4_a"}),
    ("Article 6", set()),            # article-level: names too much to promote
    ("Annex III", set()),
    ("registration under Article 49(2) for Annex III", {"art_49_2"}),
])
def test_query_citation_rule(query: str, expected: set[str]) -> None:
    from eu_ai_check.retrieval.retriever import _query_citations
    assert _query_citations(query) == expected


CITE = RetrievalConfig(mode=Mode.HYBRID, citation_priority=True)


@pytest.mark.parametrize("query, expected", [
    ("Article 26(7)", ["aiact:art_26:para_7"]),
    ("Article 5(1)(h)(iii)", ["aiact:art_5:para_1:point_h"]),
    ("Article 25(3)", ["aiact:art_25:para_3:point_a", "aiact:art_25:para_3:point_b"]),
])
def test_citation_priority_puts_cited_provisions_first(retriever: Retriever, query: str, expected: list[str]) -> None:
    hits = retriever.retrieve(query, date(2026, 9, 1), k=5, config=CITE)
    assert sorted(h.provision.logical_provision_id for h in hits[: len(expected)]) == expected


def test_citation_priority_respects_the_temporal_filter(versioned_retriever: Retriever) -> None:
    """A citation is a lookup, but only among versions in force on the date."""
    hits = versioned_retriever.retrieve("Article 26(6)", BEFORE_AMENDMENT, k=5, config=CITE)
    assert hits[0].provision.versioned_provision_id == "aiact:art_26:para_6@2024-08-01"
    assert all(h.provision.text_valid_on(BEFORE_AMENDMENT) for h in hits)


def test_citation_priority_is_a_no_op_without_a_citation(retriever: Retriever) -> None:
    q = "AI that grades student essays"
    plain = retriever.retrieve(q, date(2026, 9, 1), k=5,
                               config=RetrievalConfig(mode=Mode.HYBRID, citation_priority=False))
    cited = retriever.retrieve(q, date(2026, 9, 1), k=5, config=CITE)
    assert plain == cited

"""Command line entry point.

    eu-ai-check ingest          parse -> ingest -> validate -> write data/corpus/
    eu-ai-check validate        re-validate an already written corpus
    eu-ai-check show ID         print one provision with its rules
    eu-ai-check search QUERY    hybrid retrieval as of --date (Phase 1)
    eu-ai-check eval-retrieval  run the Phase 1 retrieval evaluation harness
    eu-ai-check bench-filter    §21 filtered-HNSW benchmark (needs --qdrant-url)

Ingestion exits non-zero when an invariant is broken. A broken invariant is not a
warning to scroll past: per SYSTEM_SPEC.md §12.1 it would resurface later as a
false `unresolved` that is very hard to distinguish from a real coverage gap.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from .corpus.baseline import measure
from .corpus.ingest import ingest_corpus
from .corpus.models import Corpus
from .corpus.parser import parse_document
from .corpus.rules import build_rules
from .corpus.validation import Severity, validate_corpus

DEFAULT_SOURCE = Path("data/raw/ai_act_32024R1689.xhtml")
DEFAULT_OUT = Path("data/corpus")
DEFAULT_QUERIES = Path("data/eval/retrieval_queries.json")
DEFAULT_SYNTHETIC = Path("data/eval/synthetic_versions.json")
SOURCE_REFERENCE = (
    "Regulation (EU) 2024/1689 (Artificial Intelligence Act), consolidated XHTML "
    "from the EU Publications Office (CELEX 32024R1689)"
)


def _build(source: Path) -> tuple[Corpus, list]:
    articles = parse_document(source)
    provisions, issues = ingest_corpus(articles)
    rules = build_rules(provisions)
    corpus = Corpus(
        source_reference=SOURCE_REFERENCE,
        provisions=provisions,
        applicability_rules=rules,
    )
    return corpus, issues


def _print_report(corpus: Corpus, issues: list, report) -> None:
    print(f"source:      {corpus.source_reference}")
    print(f"provisions:  {len(corpus.provisions)}")
    print(f"rules:       {len(corpus.applicability_rules)}")
    print(f"checks run:  {', '.join(report.checks_run)}")

    if issues:
        print(f"\ningest issues ({len(issues)}):")
        for issue in issues:
            print(f"  - {issue.kind}: {issue.detail}")

    gaps = report.declared_gaps
    if gaps:
        print(f"\ndeclared gaps ({len(gaps)}) -- permitted, reported so they stay visible:")
        for finding in gaps:
            print(f"  - {finding.check}: {finding.subject} -- {finding.detail}")

    errors = report.errors
    if errors:
        print(f"\nINVARIANT VIOLATIONS ({len(errors)}):")
        for finding in errors:
            print(f"  - {finding.check}: {finding.subject} -- {finding.detail}")
    else:
        print("\nall invariants hold.")


def cmd_ingest(args: argparse.Namespace) -> int:
    corpus, issues = _build(args.source)
    report = validate_corpus(corpus)
    _print_report(corpus, issues, report)

    if not report.ok:
        print("\nnot writing corpus: ingestion failed validation.", file=sys.stderr)
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    provisions_path = args.out / "provisions.json"
    rules_path = args.out / "applicability_rules.json"
    provisions_path.write_text(
        json.dumps(
            [p.model_dump(mode="json") for p in corpus.provisions],
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    rules_path.write_text(
        json.dumps(
            [r.model_dump(mode="json") for r in corpus.applicability_rules],
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"\nwrote {provisions_path} and {rules_path}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    corpus, issues = _build(args.source)
    report = validate_corpus(corpus)
    _print_report(corpus, issues, report)
    return 0 if report.ok else 1


def cmd_baseline(args: argparse.Namespace) -> int:
    corpus, _ = _build(args.source)
    print(measure(corpus).render())
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    corpus, _ = _build(args.source)
    matches = [
        p for p in corpus.provisions
        if args.provision_id in p.logical_provision_id
    ]
    if not matches:
        print(f"no provision matching {args.provision_id!r}", file=sys.stderr)
        return 1
    for p in matches:
        print(f"\n{'=' * 72}")
        print(f"{p.citation()}   [{p.provision_type.value}]")
        print(f"logical:   {p.logical_provision_id}")
        print(f"versioned: {p.versioned_provision_id}")
        print(f"content:   {p.content_id[:16]}...")
        print(f"text valid from {p.text_valid_from} to {p.text_valid_to or 'present'}")
        if p.cross_references:
            print(f"refs:      {', '.join(p.cross_references)}")
        rules = corpus.rules_for(p.logical_provision_id)
        for r in rules:
            window = f"{r.applicable_from} -> {r.applicable_to or 'open'}"
            print(f"rule:      {window}  {r.conditions}  [{r.source_reference}]")
        print(f"\n{p.text}")
    return 0


def _retriever(provisions, *, rerank: bool, qdrant_url: str | None = None, embeddings=None):
    # Imported lazily: loading torch costs seconds, and ingest/validate don't need it.
    from .retrieval.models import CrossEncoderReranker, SentenceTransformerEncoder
    from .retrieval.retriever import Retriever

    encoder = embeddings.encoder if embeddings else SentenceTransformerEncoder()
    return Retriever(
        provisions,
        encoder,
        CrossEncoderReranker() if rerank else None,
        qdrant_url=qdrant_url,
        embeddings=embeddings,
    )


def cmd_search(args: argparse.Namespace) -> int:
    from .retrieval.retriever import Mode, RetrievalConfig, TemporalMode

    corpus = Corpus.load(args.corpus)
    retriever = _retriever(corpus.provisions, rerank=args.rerank, qdrant_url=args.qdrant_url)
    config = RetrievalConfig(
        mode=Mode(args.mode),
        rerank=args.rerank,
        citation_priority=not args.no_citation_priority,
        temporal=TemporalMode(args.temporal),
    )
    hits = retriever.retrieve(args.query, args.date, k=args.k, config=config)
    print(f"{config.label}  relevant_date={args.date}  {len(hits)} result(s)")
    if not hits and config.temporal == TemporalMode.PRE_FILTER:
        print("no provision text was in force on that date")
    for h in hits:
        p = h.provision
        print(f"\n{h.rank}. {p.citation()}   [{p.provision_type.value}]  score={h.score:.4f}")
        print(f"   {p.versioned_provision_id}")
        print(f"   matched chunks: {', '.join(c.rsplit('#', 1)[1] for c in h.matched_chunk_ids)}")
        print(f"   {p.text[:args.chars]}{'...' if len(p.text) > args.chars else ''}")
    return 0


def cmd_eval_retrieval(args: argparse.Namespace) -> int:
    from .retrieval.evaluation import (
        RANKING_ARMS,
        TEMPORAL_ARMS,
        apply_synthetic_versions,
        load_queries,
        render_report,
        run_arm,
        summary_json,
    )

    corpus = Corpus.load(args.corpus)
    queries = load_queries(args.queries)
    real_queries = [q for q in queries if q.category != "temporal"]
    temporal_queries = [q for q in queries if q.category == "temporal"]

    real = _retriever(corpus.provisions, rerank=True)
    synthetic = _retriever(
        apply_synthetic_versions(corpus.provisions, args.synthetic),
        rerank=False,
        embeddings=real.embeddings,
    )
    synthetic.reranker = real.reranker  # share one loaded cross-encoder

    ranking = [run_arm(real, real_queries, c) for c in RANKING_ARMS]
    temporal = [run_arm(synthetic, temporal_queries, c) for c in TEMPORAL_ARMS]
    print(render_report(ranking, temporal, show_misses=args.misses))
    print(
        f"\nembedding dedup: {real.embeddings.passages_encoded} passages encoded for "
        f"{len(real.chunk_list) + len(synthetic.chunk_list)} index documents across both "
        f"indexes (each distinct text computed once, SYSTEM_SPEC.md §15)"
    )
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(summary_json(ranking, temporal), indent=2), encoding="utf-8")
        print(f"wrote {args.out}")
    return 0


def cmd_bench_filter(args: argparse.Namespace) -> int:
    from .retrieval.chunking import chunk_corpus
    from .retrieval.evaluation import apply_synthetic_versions, load_queries
    from .retrieval.filter_bench import bench_corpus, bench_selectivity, render
    from .retrieval.models import DedupEmbeddingCache, SentenceTransformerEncoder

    corpus = Corpus.load(args.corpus)
    queries = load_queries(args.queries)
    cache = DedupEmbeddingCache(SentenceTransformerEncoder())
    corpus_results = []
    for label, provisions, qs in (
        ("real", corpus.provisions, [q for q in queries if q.category != "temporal"]),
        ("versioned", apply_synthetic_versions(corpus.provisions, args.synthetic),
         [q for q in queries if q.category == "temporal"]),
    ):
        chunks = chunk_corpus(provisions)
        vectors = cache.embed([(c.content_hash, c.index_text) for c in chunks])
        qvecs = [(cache.encoder.encode_query(q.query), q.relevant_date) for q in qs]
        corpus_results += bench_corpus(label, chunks, vectors, qvecs, args.qdrant_url)
    sweep = [] if args.skip_sweep else bench_selectivity(args.qdrant_url)
    report = render(corpus_results, sweep)
    print(report)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report + "\n", encoding="utf-8")
        print(f"wrote {args.out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="eu-ai-check", description=__doc__)
    parser.add_argument(
        "--source", type=Path, default=DEFAULT_SOURCE,
        help=f"official XHTML source (default: {DEFAULT_SOURCE})",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest", help="ingest, validate and write the corpus")
    p_ingest.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p_ingest.set_defaults(func=cmd_ingest)

    p_validate = sub.add_parser("validate", help="ingest and validate without writing")
    p_validate.set_defaults(func=cmd_validate)

    p_baseline = sub.add_parser(
        "baseline", help="report Phase 0 measured baselines (SYSTEM_SPEC.md §18)"
    )
    p_baseline.set_defaults(func=cmd_baseline)

    p_show = sub.add_parser("show", help="print provisions matching an id substring")
    p_show.add_argument("provision_id")
    p_show.set_defaults(func=cmd_show)

    p_search = sub.add_parser("search", help="retrieve provisions for a query (Phase 1)")
    p_search.add_argument("query")
    p_search.add_argument("--date", type=date.fromisoformat, default=date.today(),
                          help="relevant_date (default: today)")
    p_search.add_argument("--k", type=int, default=5)
    p_search.add_argument("--mode", choices=["vector", "bm25", "hybrid"], default="hybrid")
    p_search.add_argument("--temporal", choices=["pre_filter", "post_filter", "none"],
                          default="pre_filter")
    p_search.add_argument("--rerank", action="store_true",
                          help="cross-encoder rerank (off by default; it lowered recall in eval)")
    p_search.add_argument("--no-citation-priority", action="store_true",
                          help="plain RRF: do not promote explicitly cited provisions")
    p_search.add_argument("--chars", type=int, default=240, help="text preview length")
    p_search.add_argument("--corpus", type=Path, default=DEFAULT_OUT)
    p_search.add_argument("--qdrant-url", default=None,
                          help="Qdrant server URL (default: embedded local mode)")
    p_search.set_defaults(func=cmd_search)

    p_eval = sub.add_parser("eval-retrieval", help="run the Phase 1 retrieval eval harness")
    p_eval.add_argument("--corpus", type=Path, default=DEFAULT_OUT)
    p_eval.add_argument("--queries", type=Path, default=DEFAULT_QUERIES)
    p_eval.add_argument("--synthetic", type=Path, default=DEFAULT_SYNTHETIC)
    p_eval.add_argument("--out", type=Path, default=None, help="write summary JSON here")
    p_eval.add_argument("--misses", action="store_true", help="list per-query misses")
    p_eval.set_defaults(func=cmd_eval_retrieval)

    p_bench = sub.add_parser("bench-filter", help="§21 filtered-HNSW benchmark on a Qdrant server")
    p_bench.add_argument("--qdrant-url", required=True)
    p_bench.add_argument("--corpus", type=Path, default=DEFAULT_OUT)
    p_bench.add_argument("--queries", type=Path, default=DEFAULT_QUERIES)
    p_bench.add_argument("--synthetic", type=Path, default=DEFAULT_SYNTHETIC)
    p_bench.add_argument("--skip-sweep", action="store_true")
    p_bench.add_argument("--out", type=Path, default=None)
    p_bench.set_defaults(func=cmd_bench_filter)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

"""Command line entry point.

    eu-ai-check ingest          parse -> ingest -> validate -> write data/corpus/
    eu-ai-check validate        re-validate an already written corpus
    eu-ai-check show ID         print one provision with its rules
    eu-ai-check search QUERY    hybrid retrieval as of --date (Phase 1)
    eu-ai-check eval-retrieval  run the Phase 1 retrieval evaluation harness
    eu-ai-check bench-filter    §21 filtered-HNSW benchmark (needs --qdrant-url)
    eu-ai-check extract TEXT    extract case facts from a description (Phase 2)
    eu-ai-check eval-extraction run the Phase 2 extraction evaluation harness
    eu-ai-check eval-generation the Phase 4 generation harness (see generation/cli.py)

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
from .extraction.extractor import DEFAULT_EFFORT

DEFAULT_SOURCE = Path("data/raw/ai_act_32024R1689.xhtml")
DEFAULT_OUT = Path("data/corpus")
DEFAULT_QUERIES = Path("data/eval/retrieval_queries.json")
DEFAULT_SYNTHETIC = Path("data/eval/synthetic_versions.json")
DEFAULT_EXTRACTION_CASES = Path("data/eval/extraction_cases.json")
DEFAULT_DETERMINATION_CASES = Path("data/eval/determination_cases.json")
DEFAULT_TAU = Path("data/eval/determination_tau.json")
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


def cmd_extract(args: argparse.Namespace) -> int:
    from .extraction.providers import create_extractor
    from .extraction.schema import Case

    result = create_extractor(args.provider, effort=args.effort).extract(args.text, args.date)
    print(f"provider={result.provider} model={result.requested_model} effort={result.effort}  "
          f"served by {result.served_model}  fallback={result.fell_back}  "
          f"stop={result.stop_reason}  tokens in/out={result.input_tokens}/{result.output_tokens}")
    for leak in result.leaks:
        print(f"CONCLUSION LEAK in {leak.field}: [{leak.kind}] {leak.excerpt}")
    if not result.ok:
        print(f"extraction failed: {result.error}", file=sys.stderr)
        return 1
    case = Case.from_facts(result.facts, args.text, args.date)
    print(case.model_dump_json(indent=2))
    return 1 if result.leaks else 0


def cmd_eval_extraction(args: argparse.Namespace) -> int:
    import openai

    from .extraction.evaluation import (
        JUDGE_EFFORT,
        JUDGE_MODEL,
        load_cases,
        render,
        run_arm,
        run_records,
        summary_json,
    )
    from .extraction.openai_extractor import OPENAI_MODEL, OpenAIExtractor

    # The whole LLM evaluation stack is OpenAI: one client, OPENAI_API_KEY only.
    # Extractor (system under test): gpt-6.1-sol. Judge (secondary, optional):
    # gpt-6-astra. --no-judge runs the primary evaluation with the extractor alone.
    cases = load_cases(args.cases)
    client = openai.OpenAI()
    judge_client = None if args.no_judge else client
    print(f"extractor: {OPENAI_MODEL}; judge: "
          f"{'disabled' if args.no_judge else f'{JUDGE_MODEL} (effort={JUDGE_EFFORT})'}",
          file=sys.stderr)
    variants = args.prompt_variants.split(",")
    arms = []
    for variant in variants:
        for effort in args.efforts.split(","):
            extractor = OpenAIExtractor(client, effort=effort, prompt_variant=variant)
            # The default run (baseline prompt only) keeps the first run's labels.
            label = (f"{OPENAI_MODEL} effort={extractor.effort}" if variants == ["baseline"]
                     else f"{OPENAI_MODEL} prompt={variant} effort={extractor.effort}")
            print(f"running {label}: {len(cases)} cases x {args.repeats} repeats", file=sys.stderr)
            arms.append(run_arm(label, extractor, cases, args.repeats, judge_client=judge_client))
    report = render(arms)
    print(report)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.with_suffix(".txt").write_text(report + "\n", encoding="utf-8")
        args.out.with_suffix(".json").write_text(json.dumps(summary_json(arms), indent=2), encoding="utf-8")
        runs_path = args.out.with_suffix(".runs.jsonl")
        runs_path.write_text(
            "".join(json.dumps(r, ensure_ascii=False, default=str) + "\n" for r in run_records(arms)),
            encoding="utf-8",
        )
        print(f"wrote {args.out.with_suffix('.txt')}, .json and {runs_path.name}", file=sys.stderr)
    return 1 if any(a.leaks() for a in arms) else 0


def cmd_match(args: argparse.Namespace) -> int:
    """Run the provision matcher on one description and report verdicts, tokens
    and cost. Used for the pre-run smoke test; never on eval-set descriptions."""
    import openai

    from .determination.matcher import MATCHES, OpenAIMatcher
    from .determination.scopes import candidates

    corpus = Corpus.load(args.corpus)
    cands = candidates(corpus.provisions, args.date)
    result = OpenAIMatcher(openai.OpenAI(), effort=args.effort).match(args.text, cands)
    counts: dict[str, int] = {}
    for v in result.verdicts.values():
        counts[v.verdict] = counts.get(v.verdict, 0) + 1
    print(f"served model: {result.served_model}; error: {result.error}")
    print(f"verdicts: {counts} over {len(result.verdicts)} candidates")
    for pid, v in result.verdicts.items():
        if v.verdict == MATCHES or v.downgrade_reason:
            print(f"  {pid}: {v.verdict}"
                  + (f" (downgraded from {v.model_verdict}: {v.downgrade_reason})"
                     if v.downgrade_reason else "")
                  + f" | {v.rationale}")
    cost = result.input_tokens * args.input_price / 1e6 + result.output_tokens * args.output_price / 1e6
    print(f"tokens: {result.input_tokens} in / {result.output_tokens} out "
          f"-> ${cost:.4f} at ${args.input_price}/${args.output_price} per M")
    return 0 if result.error is None else 1


def cmd_fit_tau(args: argparse.Namespace) -> int:
    from .determination.baseline import fit_tau
    from .retrieval.models import SentenceTransformerEncoder

    corpus = Corpus.load(args.corpus)
    queries = json.loads(args.queries.read_text(encoding="utf-8"))["queries"]
    fit = fit_tau(SentenceTransformerEncoder(), corpus.provisions, queries)
    print(json.dumps(fit.to_json(), indent=2))
    args.out.write_text(json.dumps(fit.to_json(), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.out}", file=sys.stderr)
    return 0


def cmd_eval_determination(args: argparse.Namespace) -> int:
    from .determination.evaluation import (
        decide,
        load_cases,
        render,
        run_arm,
        score_arm,
        score_rule_path,
    )
    from .determination.pipeline import Pipeline

    corpus = Corpus.load(args.corpus)
    pipeline = Pipeline(corpus.provisions, corpus.applicability_rules)
    cases = load_cases(args.cases)
    if args.only:
        wanted = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in wanted]
    rule_path = score_rule_path(pipeline, cases)
    arms = []
    for name in args.arms.split(","):
        if name == "baseline":
            from .determination.baseline import CutoffBaseline, load_tau
            from .retrieval.models import SentenceTransformerEncoder

            matcher = CutoffBaseline(SentenceTransformerEncoder(), load_tau(args.tau))
            repeats = 1
        else:
            import openai

            from .determination.matcher import OpenAIMatcher

            matcher = OpenAIMatcher(openai.OpenAI(max_retries=args.max_retries),
                                    effort=name.removeprefix("matcher-"),
                                    prompt_variant=args.prompt_variant)
            repeats = args.repeats
        print(f"running {matcher.name}: {len(cases)} cases x {repeats}", file=sys.stderr)
        arms.append(run_arm(matcher.name, matcher, cases, repeats, pipeline,
                            workers=args.workers))
    scores = {a.name: score_arm(a, cases) for a in arms}
    decision = decide(scores, rule_path)
    report = render(scores, rule_path, decision)
    print(report)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        stem = args.out.name
        (args.out.parent / f"{stem}.txt").write_text(report + "\n", encoding="utf-8")
        (args.out.parent / f"{stem}.json").write_text(json.dumps(
            {"scores": scores, "rule_path": rule_path, "decision": decision},
            indent=2, default=str), encoding="utf-8")
        (args.out.parent / f"{stem}.runs.jsonl").write_text("".join(
            json.dumps(r.record(a.name), ensure_ascii=False, default=str) + "\n"
            for a in arms for r in a.runs), encoding="utf-8")
        print(f"wrote {stem}.txt/.json/.runs.jsonl", file=sys.stderr)
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

    p_extract = sub.add_parser("extract", help="extract case facts from a description (Phase 2)")
    p_extract.add_argument("text")
    p_extract.add_argument("--date", type=date.fromisoformat, default=date.today(),
                           help="assessment date (default: today)")
    p_extract.add_argument("--effort", default=DEFAULT_EFFORT, choices=["low", "medium"],
                           help=f"reasoning effort (default: {DEFAULT_EFFORT}, the accepted Phase 2 "
                                "configuration)")
    p_extract.add_argument("--provider", default="openai", choices=["openai", "anthropic"],
                           help="extraction provider (default: openai, gpt-6.1-sol). "
                                "'anthropic' is an optional, non-evaluated alternative "
                                "and needs the 'anthropic' extra and ANTHROPIC_API_KEY")
    p_extract.set_defaults(func=cmd_extract)

    p_match = sub.add_parser("match", help="run the provision matcher on one description "
                                           "(calls the OpenAI API)")
    p_match.add_argument("text")
    p_match.add_argument("--date", type=date.fromisoformat, default=date.today())
    p_match.add_argument("--effort", default="low", choices=["low", "medium"])
    p_match.add_argument("--corpus", type=Path, default=DEFAULT_OUT)
    p_match.add_argument("--input-price", type=float, default=2.0,
                         help="USD per million input tokens (gpt-6.1-sol: 2.0)")
    p_match.add_argument("--output-price", type=float, default=10.0,
                         help="USD per million output tokens (gpt-6.1-sol: 10.0)")
    p_match.set_defaults(func=cmd_match)

    p_tau = sub.add_parser("fit-tau", help="fit the Phase 3 baseline cutoffs (pre-registered)")
    p_tau.add_argument("--corpus", type=Path, default=DEFAULT_OUT)
    p_tau.add_argument("--queries", type=Path, default=DEFAULT_QUERIES)
    p_tau.add_argument("--out", type=Path, default=DEFAULT_TAU)
    p_tau.set_defaults(func=cmd_fit_tau)

    p_eval_d = sub.add_parser("eval-determination",
                              help="run the Phase 3 determination eval (calls the OpenAI API "
                                   "for matcher arms)")
    p_eval_d.add_argument("--cases", type=Path, default=DEFAULT_DETERMINATION_CASES)
    p_eval_d.add_argument("--corpus", type=Path, default=DEFAULT_OUT)
    p_eval_d.add_argument("--tau", type=Path, default=DEFAULT_TAU)
    p_eval_d.add_argument("--arms", default="matcher-low,baseline",
                          help="pre-registered arms; matcher-medium was dropped for "
                               "cost before the first run (SYSTEM_SPEC.md §18)")
    p_eval_d.add_argument("--repeats", type=int, default=3)
    p_eval_d.add_argument("--prompt-variant", default="baseline",
                          help="matcher prompt variant; anything but 'baseline' is a "
                               "POST-HOC experiment (SYSTEM_SPEC.md §18)")
    p_eval_d.add_argument("--workers", type=int, default=8)
    p_eval_d.add_argument("--max-retries", type=int, default=2,
                          help="OpenAI client transport retries (2 is the SDK default)")
    p_eval_d.add_argument("--only", default=None, help="comma-separated case ids")
    p_eval_d.add_argument("--out", type=Path, default=None,
                          help="output stem, e.g. data/eval/results/determination_v1_41c")
    p_eval_d.set_defaults(func=cmd_eval_determination)

    p_eval_x = sub.add_parser("eval-extraction", help="run the Phase 2 extraction eval harness")
    p_eval_x.add_argument("--cases", type=Path, default=DEFAULT_EXTRACTION_CASES)
    p_eval_x.add_argument("--repeats", type=int, default=3)
    p_eval_x.add_argument("--efforts", default="low,medium", help="comma-separated effort arms")
    p_eval_x.add_argument("--prompt-variants", default="baseline",
                          help="comma-separated prompt variants (default: baseline, the "
                               "frozen prompt; others are follow-up experiments only)")
    p_eval_x.add_argument("--no-judge", action="store_true",
                          help="skip the secondary LLM judge (gpt-6-astra); the primary "
                               "evaluation then uses only the extractor (gpt-6.1-sol)")
    p_eval_x.add_argument("--out", type=Path, default=None,
                          help="write <out>.txt, <out>.json and <out>.runs.jsonl")
    p_eval_x.set_defaults(func=cmd_eval_extraction)

    from .generation.cli import register as register_generation  # Phase 4
    register_generation(sub)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

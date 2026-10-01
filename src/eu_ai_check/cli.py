"""Phase 0 command line entry point: ingest the corpus, validate it, write it out.

    eu-ai-check ingest    parse -> ingest -> validate -> write data/corpus/
    eu-ai-check validate  re-validate an already written corpus
    eu-ai-check show ID   print one provision with its rules

Ingestion exits non-zero when an invariant is broken. A broken invariant is not a
warning to scroll past: per SYSTEM_SPEC.md §12.1 it would resurface later as a
false `unresolved` that is very hard to distinguish from a real coverage gap.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .corpus.baseline import measure
from .corpus.ingest import ingest_corpus
from .corpus.models import Corpus
from .corpus.parser import parse_document
from .corpus.rules import build_rules
from .corpus.validation import Severity, validate_corpus

DEFAULT_SOURCE = Path("data/raw/ai_act_32024R1689.xhtml")
DEFAULT_OUT = Path("data/corpus")
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

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

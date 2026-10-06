"""Shared loaders for the Phase 4 tests: real cached records through the adapter."""

from __future__ import annotations

import json
from datetime import date
from functools import lru_cache
from pathlib import Path

from eu_ai_check.corpus.models import Corpus
from eu_ai_check.determination.evaluation import relevant_date
from eu_ai_check.determination.pipeline import Pipeline
from eu_ai_check.extraction.schema import CaseFacts
from eu_ai_check.generation.adapter import GenerationInput, adapt, replay, replay_synthetic

RUN = Path("data/eval/results/determination_v1_41c_retry.runs.jsonl")
CASES = Path("data/eval/determination_cases.json")


@lru_cache(maxsize=1)
def corpus() -> Corpus:
    return Corpus.load(Path("data/corpus"))


@lru_cache(maxsize=1)
def pipeline() -> Pipeline:
    c = corpus()
    return Pipeline(c.provisions, c.applicability_rules)


@lru_cache(maxsize=1)
def cases() -> dict[str, dict]:
    return {c["id"]: c for c in json.loads(CASES.read_text(encoding="utf-8"))["cases"]}


@lru_cache(maxsize=1)
def records() -> dict[str, dict]:
    recs = [json.loads(l) for l in RUN.read_text(encoding="utf-8").splitlines() if l]
    return {f"{r['case']}#{r['repeat']}": r for r in recs if r["arm"] == "matcher-low"}


def corpus_texts() -> dict[str, str]:
    return {p.versioned_provision_id: p.text for p in corpus().provisions}


@lru_cache(maxsize=None)
def load_input(ref: str) -> GenerationInput:
    record = records()[ref]
    case = cases()[record["case"]]
    on = relevant_date(case)
    det = replay(pipeline(), record, case, on)
    return adapt(det, CaseFacts.model_validate(case["gold_facts"]), on, corpus(), pipeline(), ref)


@lru_cache(maxsize=None)
def load_synthetic(case_id: str, on: str, ref: str) -> GenerationInput:
    case = cases()[case_id]
    d = date.fromisoformat(on)
    det = replay_synthetic(pipeline(), case, d)
    return adapt(det, CaseFacts.model_validate(case["gold_facts"]), d, corpus(), pipeline(), ref)

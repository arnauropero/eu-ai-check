"""Phase 4 eval harness: smoke test, dev iteration, and the single held-out pass.

Pre-registration 33cf62c (SYSTEM_SPEC.md §18) governs everything here:
- the held-out pass runs exactly once. A marker is written before its first call,
  so even a crashed pass cannot be re-run;
- API failures in the held-out pass count as failures and are not retried. The
  answer falls back to the template and the record is not counted as a raw pass;
- held-out output is written to its runs file (the rating sheet needs it), but only
  counts are printed;
- the adoption rule reads hygiene, coverage and (later) the pairwise rating.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .adapter import GenerationInput
from .checks import CHECKS, coverage_counts, run_checks
from .generator import (
    PROMPT_DIFFS,
    PROMPT_HISTORY,
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    OpenAIGenerator,
    prompt_sha,
    user_message,
)
from .inputs import Inputs, Item
from .render import render
from .spend import BudgetExceeded, SpendTracker
from .system_language import claims_of, system_language

PREREGISTERED_MODEL = "gpt-6.1-sol"
PREREGISTERED_EFFORT = "low"
RESULTS = Path("data/eval/results")
HELDOUT_RUNS = RESULTS / "generation_heldout.runs.jsonl"
HELDOUT_SUMMARY = RESULTS / "generation_heldout.json"
HELDOUT_MARKER = RESULTS / "generation_heldout.started"

#: Adoption-rule thresholds (APPROVED, §18).
RAW_PASS_MIN = 0.95
FALLBACK_MAX = 0.05
COVERAGE_MIN = 0.90
PAIRWISE_MIN = 15          # of 20, ties to the template


@dataclass
class ItemResult:
    ref: str
    gold_differs: bool
    synthetic: bool
    api_error: str | None = None
    generation_error: str | None = None
    raw_passed: bool = False
    raw_failed_checks: list[str] = field(default_factory=list)
    raw_failures: dict[str, list[str]] = field(default_factory=dict)
    fallback: bool = True
    shipped_passed: bool = False
    status_reproduced: bool = False
    covered: int = 0
    explainable: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int | None = None
    finish_reason: str | None = None
    #: C4/C8 matched phrases with their claims (reported only, §18 amendment).
    c4_diagnostics: list[dict] = field(default_factory=list)
    raw: str | None = None
    template_text: str = ""
    shipped_text: str = ""


def request_chars(inp: GenerationInput) -> int:
    return len(SYSTEM_PROMPT) + len(user_message(inp))


def _audit(result) -> dict[str, Any]:
    """What the ledger keeps per call besides spend (prompt versioning, raw usage)."""
    return {"prompt_version": PROMPT_VERSION, "prompt_sha": prompt_sha(),
            "prompt_note": PROMPT_HISTORY[PROMPT_VERSION],
            "prompt_diff": PROMPT_DIFFS.get(PROMPT_VERSION),
            "request_sha": getattr(result, "request_sha", None),
            "response_id": getattr(result, "response_id", None),
            "usage_raw": getattr(result, "usage_raw", None)}


def evaluate_item(item: Item, generator: OpenAIGenerator, tracker: SpendTracker,
                  phase: str, texts: dict[str, str]) -> ItemResult:
    """One call, checked. BudgetExceeded propagates: it is a hard abort."""
    inp = item.input
    out = ItemResult(item.ref, item.gold_differs, item.synthetic)
    tracker.guard(phase, request_chars(inp), generator.max_output_tokens)
    answer = None
    try:
        result = generator.generate(inp)
    except Exception as exc:  # transport / API error: recorded, counted as a failure
        out.api_error = f"{type(exc).__name__}: {str(exc)[:160]}"
        tracker.record(phase, item.ref, generator.model, 0, 0, error=out.api_error,
                       **_audit(None))
        raw_report = None
    else:
        tracker.record(phase, item.ref, result.served_model or generator.model,
                       result.input_tokens, result.output_tokens, error=result.error,
                       finish_reason=result.finish_reason,
                       reasoning_tokens=result.reasoning_tokens, **_audit(result))
        out.input_tokens, out.output_tokens = result.input_tokens, result.output_tokens
        out.reasoning_tokens, out.finish_reason = result.reasoning_tokens, result.finish_reason
        out.raw, out.generation_error, answer = result.raw, result.error, result.answer
        raw_report = run_checks(inp, render(inp, answer) if answer else render(inp), answer,
                                parse_error=result.error if answer is None else None,
                                corpus_texts=texts)
        if answer is None and result.error is None:  # pragma: no cover - defensive
            raw_report.fail("C0", "no answer and no error")
        out.raw_failures = {c: v for c, v in raw_report.failures.items() if v}
        out.c4_diagnostics = raw_report.diagnostics
        out.raw_failed_checks = raw_report.failed_checks()
        out.raw_passed = raw_report.passed and out.api_error is None
    shipped_answer = answer if out.raw_passed else None
    out.fallback = shipped_answer is None
    shipped = render(inp, shipped_answer)
    shipped_report = run_checks(inp, shipped, shipped_answer, corpus_texts=texts)
    out.shipped_passed = shipped_report.passed
    by_id = {d.determination_id: d for d in inp.determinations}
    out.status_reproduced = all(
        (s.status, s.reason) == (by_id[s.determination_id].applicability_status,
                                 by_id[s.determination_id].not_applicable_reason)
        for s in shipped.sections)
    out.covered, out.explainable = coverage_counts(inp, shipped_answer, shipped_report)
    out.template_text = render(inp).to_text()
    out.shipped_text = shipped.to_text()
    return out


def summarise(results: list[ItemResult]) -> dict[str, Any]:
    n = len(results)
    scored = [r for r in results if not r.synthetic] or results

    def rate(xs: list[bool]) -> float | None:
        return round(sum(xs) / len(xs), 4) if xs else None

    failed_by_check = {c: sum(c in r.raw_failed_checks for r in results) for c in CHECKS}
    finish = Counter(r.finish_reason or ("api_error" if r.api_error else "none") for r in results)
    truncated = sum((r.finish_reason or "").startswith("incomplete:max_output_tokens")
                    for r in results)
    lexicon_hits = Counter(f"{d['check']}:{d.get('lexicon', d['kind'])}"
                           for r in results for d in r.c4_diagnostics)
    explainable = sum(r.explainable for r in scored)
    gold = [r for r in results if r.gold_differs]
    return {
        "records": n,
        "api_errors": sum(r.api_error is not None for r in results),
        "raw_pass_rate": rate([r.raw_passed for r in results]),
        "fallback_rate": rate([r.fallback for r in results]),
        "raw_failed_by_check": failed_by_check,
        "finish_reasons": dict(finish),
        "truncated": truncated,
        "content_failures": sum(not r.raw_passed and r.finish_reason == "completed"
                                for r in results),
        "c4_c8_diagnostic_hits": dict(lexicon_hits),
        "c4_violations": failed_by_check["C4"],
        "c8_violations": failed_by_check["C8"],
        "shipped_pass_rate": rate([r.shipped_passed for r in results]),
        "grounded_explanation_coverage": (round(sum(r.covered for r in scored) / explainable, 4)
                                          if explainable else None),
        "gold_differs_records": len(gold),
        "faithful_reproduction_rate": rate([r.status_reproduced for r in gold]),
        "status_reproduced_all": all(r.status_reproduced for r in results),
        # Reported only, never a gate (system_language.py).
        "system_language": system_language(claims_of(asdict(r) for r in results)),
        "input_tokens": sum(r.input_tokens for r in results),
        "reasoning_tokens": sum(r.reasoning_tokens or 0 for r in results),
        "output_tokens": sum(r.output_tokens for r in results),
    }


def decide(summary: dict[str, Any], pairwise_llm_wins: int | None = None) -> dict[str, Any]:
    hygiene = {
        "raw_pass_rate>=0.95": (summary["raw_pass_rate"] or 0) >= RAW_PASS_MIN,
        "fallback_rate<=0.05": (summary["fallback_rate"] if summary["fallback_rate"] is not None
                                else 1) <= FALLBACK_MAX,
        "zero_C4": summary["c4_violations"] == 0,
        "zero_C8": summary["c8_violations"] == 0,
    }
    coverage_ok = (summary["grounded_explanation_coverage"] or 0) >= COVERAGE_MIN
    pairwise_ok = None if pairwise_llm_wins is None else pairwise_llm_wins >= PAIRWISE_MIN
    acceptance = {
        "shipped_pass_rate==1.00": summary["shipped_pass_rate"] == 1.0,
        "faithful_reproduction==1.00": summary["faithful_reproduction_rate"] == 1.0,
    }
    adopt = all(hygiene.values()) and coverage_ok and pairwise_ok is True
    if not all(hygiene.values()) or not coverage_ok or pairwise_ok is False:
        shipped = "template"
    else:
        shipped = "llm" if adopt else "pending pairwise rating"
    return {"hygiene": hygiene, "coverage>=0.90": coverage_ok,
            "pairwise>=15/20": pairwise_ok, "adopt_llm": adopt,
            "phase4_generator": shipped, "acceptance": acceptance,
            "phase4_acceptance_met": all(acceptance.values())}


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _meta(generator: OpenAIGenerator, inputs: Inputs) -> dict[str, Any]:
    split_sha = hashlib.sha256(json.dumps(inputs.split, sort_keys=True, ensure_ascii=False)
                               .encode("utf-8")).hexdigest()
    return {"model": generator.model, "effort": generator.effort,
            "max_output_tokens": generator.max_output_tokens, "prompt_sha": prompt_sha(),
            "prompt_version": PROMPT_VERSION, "prompt_note": PROMPT_HISTORY[PROMPT_VERSION],
            "split_content_sha256": split_sha}


def make_tracker(inputs: Inputs, max_output_tokens: int, ledger: Path | None = None) -> SpendTracker:
    """A tracker whose held-out reserve covers the real held-out pass at its worst case."""
    worst = sum(SpendTracker.worst_case(request_chars(i.input), max_output_tokens)
                for i in inputs.side("heldout"))
    kwargs = {"ledger": ledger} if ledger else {}
    return SpendTracker(heldout_worst_case=worst, **kwargs)


# --------------------------------------------------------------------------
# Smoke test: one call on the real dev record with the most determinations and
# the largest request (amendment 2026-10-06, §18). It counts against dev budget.
# --------------------------------------------------------------------------

def smoke_item(inputs: Inputs) -> Item:
    items = inputs.side("dev", repeats=None, synthetic=False)
    return max(items, key=lambda i: (len(i.input.determinations), request_chars(i.input), i.ref))


def run_smoke(generator: OpenAIGenerator, tracker: SpendTracker,
              inputs: Inputs) -> ItemResult:
    return evaluate_item(smoke_item(inputs), generator, tracker, "smoke", inputs.corpus_texts())


# --------------------------------------------------------------------------
# Dev iteration (results may be read freely)
# --------------------------------------------------------------------------

def run_dev(generator: OpenAIGenerator, tracker: SpendTracker, inputs: Inputs, label: str,
            repeats: tuple[int, ...] = (0,), refs: list[str] | None = None,
            out_dir: Path = RESULTS) -> dict[str, Any]:
    items = inputs.side("dev", repeats=repeats)
    if refs:
        items = [i for i in items if i.ref in set(refs)]
    texts = inputs.corpus_texts()
    results: list[ItemResult] = []
    stopped = None
    for item in items:
        try:
            results.append(evaluate_item(item, generator, tracker, "dev", texts))
        except BudgetExceeded as exc:  # hard stop: dev may never eat the reserve
            stopped = str(exc)
            break
    summary = {**summarise(results), "stopped_by_budget": stopped,
               "spent_usd": round(tracker.spent, 4), **_meta(generator, inputs)}
    _write_jsonl(out_dir / f"generation_dev_{label}.runs.jsonl", [asdict(r) for r in results])
    (out_dir / f"generation_dev_{label}.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


# --------------------------------------------------------------------------
# The single held-out pass
# --------------------------------------------------------------------------

class HeldoutAlreadyRun(RuntimeError):
    pass


def run_heldout(generator: OpenAIGenerator, tracker: SpendTracker, inputs: Inputs, *,
                confirm: bool, results_dir: Path = RESULTS) -> dict[str, Any]:
    """Exactly once. Prints nothing but counts (the caller prints the summary)."""
    runs, summary_path = results_dir / HELDOUT_RUNS.name, results_dir / HELDOUT_SUMMARY.name
    marker = results_dir / HELDOUT_MARKER.name
    if not confirm:
        raise HeldoutAlreadyRun("the held-out pass needs explicit confirmation (--confirm-heldout)")
    if marker.exists() or runs.exists() or tracker.calls("heldout"):
        raise HeldoutAlreadyRun("the held-out pass has already been started; it runs exactly once")
    items = inputs.side("heldout")
    if len(items) != 39:
        raise RuntimeError(f"expected 39 held-out records, got {len(items)}")
    tracker.preflight_heldout(generator.max_output_tokens, [request_chars(i.input) for i in items])
    results_dir.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps(_meta(generator, inputs)) + "\n", encoding="utf-8")

    texts = inputs.corpus_texts()
    results: list[ItemResult] = []
    budget_stop = None
    for item in items:
        if budget_stop:
            r = ItemResult(item.ref, item.gold_differs, False, api_error=f"not called: {budget_stop}")
            results.append(r)
            continue
        try:
            results.append(evaluate_item(item, generator, tracker, "heldout", texts))
        except BudgetExceeded as exc:  # should be impossible after the preflight
            budget_stop = str(exc)
            results.append(ItemResult(item.ref, item.gold_differs, False,
                                      api_error=f"not called: {budget_stop}"))
    summary = summarise(results)
    out = {**summary, "decision": decide(summary), "spent_usd": round(tracker.spent, 4),
           "budget_stop": budget_stop, **_meta(generator, inputs)}
    _write_jsonl(runs, [asdict(r) for r in results])
    summary_path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    return out


def counts_only(summary: dict[str, Any]) -> str:
    keep = ("records", "api_errors", "raw_pass_rate", "fallback_rate", "raw_failed_by_check",
            "finish_reasons", "truncated", "content_failures", "c4_c8_diagnostic_hits",
            "c4_violations", "c8_violations", "shipped_pass_rate",
            "grounded_explanation_coverage", "faithful_reproduction_rate", "spent_usd",
            "decision", "stopped_by_budget", "budget_stop")
    out = {k: summary[k] for k in keep if k in summary}
    if "system_language" in summary:   # counts only: no phrases of held-out claims
        out["system_language"] = {k: v for k, v in summary["system_language"].items()
                                  if k != "affected"}
    return json.dumps(out, indent=2)

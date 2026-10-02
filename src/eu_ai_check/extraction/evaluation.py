"""Extraction evaluation harness (PROJECT_PLAN.md §6 Phase 2, SYSTEM_SPEC.md §18).

Scoring, as fixed in `data/eval/extraction_cases.json` before the first run:

  * booleans and dates: exact match, null included;
  * free text (PRIMARY): concept groups -- every group must be matched by at
    least one of its stems; an expected null must be predicted null;
  * free text (SECONDARY): an LLM judge decides whether the extracted value
    states the same fact as the label's reference sentence. It never overrides
    the primary metric; it exists so the two can be compared, and every
    disagreement is listed for inspection;
  * zero tolerance: any conclusion field or conclusion term in any output is a
    hard failure, reported separately from accuracy.

Each case runs `repeats` times per arm, because the model is not deterministic:
a field that is right on one run and wrong on the next is reported as unstable
rather than averaged into a flattering mean.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from .extractor import FALLBACK_BETA, MODEL, ExtractionResult, Extractor
from .schema import BOOL_FIELDS, DATE_FIELDS, SHORT_TEXT_FIELDS, TEXT_FIELDS

CONCEPT_FIELDS = (*TEXT_FIELDS, *SHORT_TEXT_FIELDS)
ALL_SCORED = (*CONCEPT_FIELDS, *BOOL_FIELDS, *DATE_FIELDS)


@dataclass(frozen=True)
class EvalCase:
    id: str
    category: str
    assessment_date: date
    description: str
    expected: dict[str, Any]
    post_hoc: bool = False


def load_cases(path: Path) -> list[EvalCase]:
    raw = json.loads(path.read_text(encoding="utf-8"))["cases"]
    return [
        EvalCase(
            id=c["id"], category=c["category"],
            assessment_date=date.fromisoformat(c["assessment_date"]),
            description=c["description"], expected=c["expected"],
            post_hoc=c.get("post_hoc", False),
        )
        for c in raw
    ]


def concept_match(value: str | None, label: dict[str, Any] | None) -> bool:
    if label is None:
        return value is None
    if not value:
        return False
    v = value.lower()
    return all(any(stem.lower() in v for stem in group) for group in label["concepts"])


def score_field(name: str, predicted: Any, expected: Any) -> bool:
    if name in CONCEPT_FIELDS:
        return concept_match(predicted, expected)
    if name in DATE_FIELDS:
        pred = predicted.isoformat() if isinstance(predicted, date) else predicted
        return pred == expected
    return predicted == expected


# ---------------------------------------------------------------------------
# Secondary: LLM judge
# ---------------------------------------------------------------------------

JUDGE_SYSTEM = """\
You check a fact-extraction system. For each field you get a reference statement of \
the fact and the value the system extracted. Answer whether the extracted value \
states the same fact as the reference: wording may differ, extra harmless detail is \
fine, but a missing key element, a different fact, or a contradiction is not."""

_JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "field": {"type": "string"},
                    "same_fact": {"type": "boolean"},
                    "reason": {"type": "string"},
                },
                "required": ["field", "same_fact", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["verdicts"],
    "additionalProperties": False,
}


def judge(client: Any, case: EvalCase, values: dict[str, str]) -> dict[str, tuple[bool, str]]:
    """One call per extraction, judging every text field that has both a label
    and a predicted value. Fields with an expected null are not judged: null
    discipline is scored exactly by the primary metric."""
    items = [
        f"- field: {f}\n  reference: {case.expected[f]['reference']}\n  extracted: {values[f]}"
        for f in values
    ]
    if not items:
        return {}
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=4000,
        betas=[FALLBACK_BETA],
        fallbacks="default",
        system=JUDGE_SYSTEM,
        messages=[{"role": "user", "content": "\n".join(items)}],
        output_config={
            "effort": "low",
            "format": {"type": "json_schema", "schema": _JUDGE_SCHEMA},
        },
    )
    text = next((b.text for b in response.content if b.type == "text"), "{}")
    verdicts = json.loads(text).get("verdicts", [])
    return {v["field"]: (v["same_fact"], v["reason"]) for v in verdicts if v["field"] in values}


# ---------------------------------------------------------------------------
# Running an arm
# ---------------------------------------------------------------------------

@dataclass
class Run:
    case: EvalCase
    repeat: int
    result: ExtractionResult
    correct: dict[str, bool] = field(default_factory=dict)
    judged: dict[str, tuple[bool, str]] = field(default_factory=dict)

    def predicted(self, name: str) -> Any:
        return getattr(self.result.facts, name) if self.result.facts else None


@dataclass
class ArmReport:
    label: str
    runs: list[Run]

    # -- accuracy ---------------------------------------------------------------

    def field_accuracy(self, name: str) -> float:
        return statistics.fmean(r.correct[name] for r in self.runs if r.result.ok)

    def overall_accuracy(self, fields: Sequence[str] = ALL_SCORED, category: str | None = None) -> float:
        cells = [
            r.correct[f] for r in self.runs if r.result.ok
            and (category is None or r.case.category == category)
            for f in fields
        ]
        return statistics.fmean(cells) if cells else float("nan")

    def null_discipline(self) -> tuple[float, float]:
        """(share of expected-null slots predicted null, share of expected-value
        slots predicted non-null)."""
        exp_null, exp_val = [], []
        for r in self.runs:
            if not r.result.ok:
                continue
            for f in ALL_SCORED:
                pred_null = r.predicted(f) is None
                (exp_null if r.case.expected[f] is None else exp_val).append(
                    pred_null if r.case.expected[f] is None else not pred_null
                )
        return statistics.fmean(exp_null), statistics.fmean(exp_val)

    def unstable(self) -> list[tuple[str, str]]:
        """(case, field) pairs whose correctness differs across repeats."""
        seen: dict[tuple[str, str], set[bool]] = defaultdict(set)
        for r in self.runs:
            if r.result.ok:
                for f in ALL_SCORED:
                    seen[(r.case.id, f)].add(r.correct[f])
        return sorted(k for k, v in seen.items() if len(v) > 1)

    # -- zero tolerance and failures --------------------------------------------

    def leaks(self) -> list[tuple[str, int, Any]]:
        return [(r.case.id, r.repeat, leak) for r in self.runs for leak in r.result.leaks]

    def failures(self) -> list[tuple[str, int, str]]:
        return [(r.case.id, r.repeat, r.result.error or "") for r in self.runs if not r.result.ok]

    # -- judge -------------------------------------------------------------------

    def judge_agreement(self) -> tuple[int, int, list[tuple[str, int, str, bool, bool, str, Any]]]:
        agree = total = 0
        disagreements = []
        for r in self.runs:
            for f, (same, reason) in r.judged.items():
                total += 1
                primary = r.correct[f]
                if primary == same:
                    agree += 1
                else:
                    disagreements.append((r.case.id, r.repeat, f, primary, same, reason, r.predicted(f)))
        return agree, total, disagreements


def run_arm(
    label: str,
    extractor: Extractor,
    cases: Sequence[EvalCase],
    repeats: int,
    judge_client: Any | None = None,
    workers: int = 8,
) -> ArmReport:
    jobs = [(c, i) for c in cases for i in range(repeats)]

    def one(job: tuple[EvalCase, int]) -> Run:
        case, i = job
        run = Run(case, i, extractor.extract(case.description, case.assessment_date))
        if run.result.ok:
            run.correct = {f: score_field(f, run.predicted(f), case.expected[f]) for f in ALL_SCORED}
            if judge_client is not None:
                values = {
                    f: run.predicted(f) for f in TEXT_FIELDS
                    if case.expected[f] is not None and run.predicted(f) is not None
                }
                run.judged = judge(judge_client, case, values)
        return run

    with ThreadPoolExecutor(max_workers=workers) as pool:
        runs = list(pool.map(one, jobs))
    return ArmReport(label, runs)


def render(arms: Sequence[ArmReport], *, show_errors: bool = True) -> str:
    lines = ["Phase 2 extraction evaluation", "=" * 96, ""]
    for arm in arms:
        n_ok = sum(r.result.ok for r in arm.runs)
        served = Counter(r.result.served_model for r in arm.runs)
        fell_back = sum(r.result.fell_back for r in arm.runs)
        tokens_in = sum(r.result.input_tokens for r in arm.runs)
        tokens_out = sum(r.result.output_tokens for r in arm.runs)
        lines.append(f"## {arm.label}   runs={len(arm.runs)} ok={n_ok}  served={dict(served)}  "
                     f"fallbacks={fell_back}  tokens in/out={tokens_in}/{tokens_out}")
        leaks = arm.leaks()
        lines.append(f"  ZERO-TOLERANCE conclusion leaks: {len(leaks)}"
                     + ("  <-- HARD FAILURE" if leaks else ""))
        for cid, rep, leak in leaks:
            lines.append(f"    {cid}#{rep} {leak.field}: [{leak.kind}] ...{leak.excerpt}...")
        for cid, rep, err in arm.failures():
            lines.append(f"  extraction failure {cid}#{rep}: {err}")
        if not n_ok:
            lines.append("")
            continue
        lines.append(f"  overall field accuracy          {arm.overall_accuracy():.3f}")
        lines.append(f"    booleans                      {arm.overall_accuracy(BOOL_FIELDS):.3f}")
        lines.append(f"    dates                         {arm.overall_accuracy(DATE_FIELDS):.3f}")
        lines.append(f"    free text (concept groups)    {arm.overall_accuracy(CONCEPT_FIELDS):.3f}")
        null_ok, value_ok = arm.null_discipline()
        lines.append(f"  null discipline: expected-null kept null {null_ok:.3f}; "
                     f"expected-value filled {value_ok:.3f}")
        lines.append("  per field:")
        for f in ALL_SCORED:
            lines.append(f"    {f:<38}{arm.field_accuracy(f):.3f}")
        lines.append("  per category:")
        for cat in sorted({r.case.category for r in arm.runs}):
            lines.append(f"    {cat:<38}{arm.overall_accuracy(category=cat):.3f}")
        unstable = arm.unstable()
        lines.append(f"  unstable (case, field) across repeats: {len(unstable)}")
        for cid, f in unstable:
            lines.append(f"    {cid} {f}")
        agree, total, dis = arm.judge_agreement()
        if total:
            lines.append(f"  judge agreement with concept groups: {agree}/{total}")
            for cid, rep, f, primary, same, reason, value in dis:
                lines.append(f"    {cid}#{rep} {f}: concepts={'ok' if primary else 'miss'} "
                             f"judge={'same' if same else 'diff'}  value={value!r}  ({reason})")
        if show_errors:
            lines.append("  wrong fields (first repeat only):")
            for r in arm.runs:
                if r.repeat == 0 and r.result.ok:
                    for f in ALL_SCORED:
                        if not r.correct[f]:
                            exp = r.case.expected[f]
                            exp = exp["reference"] if isinstance(exp, dict) else exp
                            lines.append(f"    {r.case.id} {f}: got {r.predicted(f)!r}, expected {exp!r}")
        lines.append("")
    return "\n".join(lines)


def summary_json(arms: Sequence[ArmReport]) -> dict[str, Any]:
    out = {}
    for arm in arms:
        null_ok, value_ok = arm.null_discipline() if any(r.result.ok for r in arm.runs) else (None, None)
        out[arm.label] = {
            "runs": len(arm.runs),
            "ok": sum(r.result.ok for r in arm.runs),
            "leaks": len(arm.leaks()),
            "overall": arm.overall_accuracy(),
            "booleans": arm.overall_accuracy(BOOL_FIELDS),
            "dates": arm.overall_accuracy(DATE_FIELDS),
            "free_text": arm.overall_accuracy(CONCEPT_FIELDS),
            "null_kept_null": null_ok,
            "value_filled": value_ok,
            "per_field": {f: arm.field_accuracy(f) for f in ALL_SCORED},
            "unstable": len(arm.unstable()),
            "fallbacks": sum(r.result.fell_back for r in arm.runs),
            "input_tokens": sum(r.result.input_tokens for r in arm.runs),
            "output_tokens": sum(r.result.output_tokens for r in arm.runs),
        }
    return out

"""Template-baseline check run (Phase 4 baseline, $0).

Returns counts only. Rendered texts are never returned or printed here, so the
template cannot be tuned on held-out content.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from .checks import CHECKS, coverage_counts, run_checks
from .inputs import Inputs, Item
from .render import render


def template_counts(items: list[Item], corpus_texts: dict[str, str]) -> dict[str, Any]:
    passed = 0
    failed_by_check: Counter = Counter()
    explainable = 0
    for item in items:
        report = run_checks(item.input, render(item.input), None, corpus_texts=corpus_texts)
        passed += report.passed
        for c in report.failed_checks():
            failed_by_check[c] += 1
        explainable += coverage_counts(item.input, None, report)[1]
    return {
        "records": len(items),
        "passed_all": passed,
        "failed": len(items) - passed,
        "failed_by_check": {c: failed_by_check.get(c, 0) for c in CHECKS},
        "explainable_determinations": explainable,
        "grounded_explanation_coverage": 0.0 if explainable else None,
    }


def run(inputs: Inputs | None = None) -> dict[str, Any]:
    inputs = inputs or Inputs()
    texts = inputs.corpus_texts()
    dev = inputs.side("dev", synthetic=False)
    return {
        "dev": template_counts(dev, texts),
        "dev_synthetic": template_counts(inputs.synthetic_items(), texts),
        "heldout": template_counts(inputs.side("heldout"), texts),
    }

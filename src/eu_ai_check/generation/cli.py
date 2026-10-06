"""`eu-ai-check eval-generation`: the Phase 4 harness from the command line.

    --mode template        template baseline, counts only ($0)
    --mode smoke           one call on the largest dev record (counts against dev budget)
    --mode dev             dev iteration on repeat 0 + S01-S03 (or --refs)
    --mode heldout         the single held-out pass (needs --confirm-heldout)
    --mode rating-sheet    blinded A/B sheet, after the held-out pass
    --mode score-ratings   unblind and score, after every pair is rated

Every mode that calls the API goes through the spend tracker (§18, $5 cap).
"""

from __future__ import annotations

import argparse
import json


def cmd_eval_generation(args: argparse.Namespace) -> int:
    import sys

    # The Windows console default (cp1252) would mangle non-ASCII text such as "–".
    sys.stdout.reconfigure(encoding="utf-8")
    from . import evaluation as ev
    from . import rating
    from .inputs import Inputs
    from .template_eval import run as template_run

    if args.mode == "template":
        print(json.dumps(template_run(), indent=2))
        return 0
    if args.mode == "rating-sheet":
        print(json.dumps(rating.make_sheet(), indent=2))
        return 0
    if args.mode == "score-ratings":
        from pathlib import Path

        print(json.dumps(rating.score(validation_ratings_path=Path(args.validation_ratings)),
                         indent=2))
        return 0

    from .generator import OpenAIGenerator

    inputs = Inputs()
    generator = OpenAIGenerator(model=args.model, effort=args.effort)
    tracker = ev.make_tracker(inputs, generator.max_output_tokens)
    print(f"spent so far ${tracker.spent:.4f}; held-out reserve ${tracker.reserve():.4f}; "
          f"dev budget left ${tracker.dev_budget_left():.4f}")
    if args.mode == "smoke":
        from .checks import CHECKS
        from .spend import cost

        item = ev.smoke_item(inputs)
        before = tracker.spent
        r = ev.run_smoke(generator, tracker, inputs)
        print(json.dumps({
            "record": item.ref, "determinations": len(item.input.determinations),
            "request_chars": ev.request_chars(item.input),
            "input_tokens": r.input_tokens, "output_tokens": r.output_tokens,
            "reasoning_tokens": r.reasoning_tokens, "finish_reason": r.finish_reason,
            "max_output_tokens": generator.max_output_tokens,
            "cost_usd": round(cost(r.input_tokens, r.output_tokens), 6),
            "spent_before_usd": round(before, 6), "spent_after_usd": round(tracker.spent, 6),
            "api_error": r.api_error, "generation_error": r.generation_error,
            "checks": {c: ("fail" if c in r.raw_failed_checks else "pass") for c in CHECKS}
            if r.api_error is None and r.generation_error is None else "not run (no answer)",
            "raw_failures": r.raw_failures, "c4_c8_diagnostics": r.c4_diagnostics,
            "raw_passed": r.raw_passed, "fallback": r.fallback,
            "dev_budget_left_usd": round(tracker.dev_budget_left(), 4),
        }, indent=2, ensure_ascii=False))
        print(r.shipped_text)
        return 0
    if args.mode == "dev":
        refs = args.refs.split(",") if args.refs else None
        repeats = tuple(int(x) for x in args.repeats.split(","))
        summary = ev.run_dev(generator, tracker, inputs, args.label, repeats=repeats, refs=refs)
        print(json.dumps(summary, indent=2))
        return 0
    if args.mode == "heldout":
        out = ev.run_heldout(generator, tracker, inputs, confirm=args.confirm_heldout)
        print(ev.counts_only(out))
        return 0
    raise SystemExit(f"unknown mode {args.mode}")


def register(sub) -> None:
    from .evaluation import PREREGISTERED_EFFORT, PREREGISTERED_MODEL

    p = sub.add_parser("eval-generation", help="Phase 4 generation harness")
    p.add_argument("--mode", required=True,
                   choices=["template", "smoke", "dev", "heldout", "rating-sheet",
                            "score-ratings"])
    p.add_argument("--model", default=PREREGISTERED_MODEL,
                   help=f"generation model (pre-registered: {PREREGISTERED_MODEL})")
    p.add_argument("--effort", default=PREREGISTERED_EFFORT)
    p.add_argument("--label", default="iter1", help="dev run label (results file name)")
    p.add_argument("--repeats", default="0", help="dev repeats, comma-separated")
    p.add_argument("--refs", default=None, help="dev records only, e.g. D01#0,D10#0")
    p.add_argument("--validation-ratings",
                   default="data/eval/results/generation_ratings_validation.json",
                   help="score-ratings: the user's ratings of the validation pairs "
                        "(agreement with the LLM rater, reported only)")
    p.add_argument("--confirm-heldout", action="store_true",
                   help="required for the single held-out pass")
    p.set_defaults(func=cmd_eval_generation)

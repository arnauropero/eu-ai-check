"""`eu-ai-check eval-generation`: the Phase 4 harness from the command line.

    --mode template        template baseline, counts only ($0)
    --mode smoke           one call on a made-up input (needs the user's go-ahead)
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
        print(json.dumps(rating.score(), indent=2))
        return 0

    from .generator import OpenAIGenerator

    inputs = Inputs()
    generator = OpenAIGenerator(model=args.model, effort=args.effort)
    tracker = ev.make_tracker(inputs, generator.max_output_tokens)
    print(f"spent so far ${tracker.spent:.4f}; held-out reserve ${tracker.reserve():.4f}; "
          f"dev budget left ${tracker.dev_budget_left():.4f}")
    if args.mode == "smoke":
        r = ev.run_smoke(generator, tracker, inputs)
        print(json.dumps({"raw_passed": r.raw_passed, "failed": r.raw_failures,
                          "api_error": r.api_error, "error": r.generation_error,
                          "input_tokens": r.input_tokens, "output_tokens": r.output_tokens,
                          "spent_usd": round(tracker.spent, 4)}, indent=2))
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
    p.add_argument("--confirm-heldout", action="store_true",
                   help="required for the single held-out pass")
    p.set_defaults(func=cmd_eval_generation)

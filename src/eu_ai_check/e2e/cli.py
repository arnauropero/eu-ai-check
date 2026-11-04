"""`eu-ai-check eval-e2e`: the Phase 5 runner from the command line.

    --mode status      spend so far, reserve and budgets ($0, no client)
    --mode stability   D32, D10, D35, D39 × 3, in rounds, under the $1.50 cap
    --mode heldout     the single 20-case pass (needs --confirm-heldout); add --resume
                       to continue a crashed pass (same scored-path tree hash only)
    --mode demo        record the five demo examples once (data/demo/), after the
                       held-out pass, under the same $8 cap
    --mode demo-page   re-render data/demo/recording.md from the saved recording with
                       the current titles and notes ($0, no client, outputs unchanged)

`eu-ai-check assess TEXT` runs the same frozen pipeline on your own description
(see e2e/assess.py). It writes nothing and is capped by --max-cost.

Every evaluation API call goes through the Phase 5 spend tracker ($8 cap, $3.48 held-out
reserve). No API call may be made before the §18 pre-registration is committed.
"""

from __future__ import annotations

import argparse
import json


def cmd_eval_e2e(args: argparse.Namespace) -> int:
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    from . import runner
    from .spend import E2ESpend

    tracker = E2ESpend(eval_clock=runner.EVAL_CLOCK)
    status = {"spent_usd": round(tracker.spent, 4), "heldout_reserve_usd": tracker.reserve(),
              "stability_budget_left_usd": round(tracker.budget_left("stability"), 4),
              "eval_clock": runner.EVAL_CLOCK.isoformat()}
    print(json.dumps(status))
    if args.mode == "status":
        return 0
    if args.mode == "demo-page":
        from .demo import rerender_page

        rerender_page()
        print("re-rendered data/demo/recording.md from data/demo/recording.json")
        return 0
    stages = runner.Stages.frozen(runner.default_client())
    if args.mode == "stability":
        out = runner.run_stability(stages, tracker, label=args.label)
    elif args.mode == "demo":
        from .demo import run_demo

        out = run_demo(stages, tracker)
        out = {k: v for k, v in out.items() if k != "examples"} | {
            "examples": [{k: x[k] for k in ("id", "covers", "stage_failed", "fallback",
                                            "spend_usd")} for x in out["examples"]]}
    elif args.mode == "heldout":
        out = runner.run_heldout(stages, tracker, confirm=args.confirm_heldout,
                                 resume=args.resume)
    else:
        raise SystemExit(f"unknown mode {args.mode}")
    print(json.dumps(out, indent=2, default=str, ensure_ascii=False))
    return 0


def cmd_assess(args: argparse.Namespace) -> int:
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    from . import runner
    from .assess import CostCapExceeded, assess, render

    stages = runner.Stages.frozen(runner.default_client())
    try:
        result = assess(args.text, args.date, stages, max_cost=args.max_cost)
    except CostCapExceeded as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(render(result))
    return 0 if result["answer"] is not None else 1


def register(sub) -> None:
    from datetime import date

    from .assess import DEFAULT_MAX_COST_USD

    a = sub.add_parser("assess", help="assess your own description with the full frozen "
                                      "pipeline (calls the OpenAI API)")
    a.add_argument("text", help="free-text description of the AI system and its use")
    a.add_argument("--date", type=date.fromisoformat, default=date.today(),
                   help="assessment date, YYYY-MM-DD (default: today). A date stated in "
                        "the description takes precedence")
    a.add_argument("--max-cost", type=float, default=DEFAULT_MAX_COST_USD,
                   help=f"USD cap for this run (default {DEFAULT_MAX_COST_USD}); refused "
                        "before any call if the worst case is above it")
    a.set_defaults(func=cmd_assess)

    p = sub.add_parser("eval-e2e", help="Phase 5 end-to-end runner")
    p.add_argument("--mode", required=True, choices=["status", "stability", "heldout", "demo",
                                                        "demo-page"])
    p.add_argument("--label", default="v1", help="stability results label")
    p.add_argument("--confirm-heldout", action="store_true",
                   help="required for the single held-out pass")
    p.add_argument("--resume", action="store_true",
                   help="continue a crashed held-out pass: only cases without a recorded "
                        "result, only if the scored-path tree hash matches the marker")
    p.set_defaults(func=cmd_eval_e2e)

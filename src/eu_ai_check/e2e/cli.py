"""`eu-ai-check eval-e2e`: the Phase 5 runner from the command line.

    --mode status      spend so far, reserve and budgets ($0, no client)
    --mode stability   D32, D10, D35, D39 × 3, in rounds, under the $1.50 cap
    --mode heldout     the single 20-case pass (needs --confirm-heldout)

Every API call goes through the Phase 5 spend tracker ($8 cap, $3.48 held-out
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
    stages = runner.Stages.frozen(runner.default_client())
    if args.mode == "stability":
        out = runner.run_stability(stages, tracker, label=args.label)
    elif args.mode == "heldout":
        out = runner.run_heldout(stages, tracker, confirm=args.confirm_heldout)
    else:
        raise SystemExit(f"unknown mode {args.mode}")
    print(json.dumps(out, indent=2, default=str, ensure_ascii=False))
    return 0


def register(sub) -> None:
    p = sub.add_parser("eval-e2e", help="Phase 5 end-to-end runner")
    p.add_argument("--mode", required=True, choices=["status", "stability", "heldout"])
    p.add_argument("--label", default="v1", help="stability results label")
    p.add_argument("--confirm-heldout", action="store_true",
                   help="required for the single held-out pass")
    p.set_defaults(func=cmd_eval_e2e)

"""Retry only the API-failed (case, repeat) pairs of determination_v1_41c and re-score.

Good runs are replayed from their recorded verdicts (no API call) and checked to
reproduce their original records exactly; failed pairs get one fresh live call each.
"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import openai

from eu_ai_check.cli import DEFAULT_DETERMINATION_CASES, DEFAULT_OUT
from eu_ai_check.corpus.models import Corpus
from eu_ai_check.determination.evaluation import (
    ArmRuns, Run, decide, load_cases, relevant_date, render, score_arm, score_rule_path)
from eu_ai_check.determination.matcher import MatchResult, OpenAIMatcher, Verdict
from eu_ai_check.determination.pipeline import Pipeline
from eu_ai_check.extraction.schema import CaseFacts

RESULTS = Path("data/eval/results")
SRC = RESULTS / "determination_v1_41c.runs.jsonl"
OUT = RESULTS / "determination_v1_41c_retry"


class Replay:
    def __init__(self, rec):
        self.rec = rec
        self.name = "replay"

    def match(self, description, candidates):
        r = self.rec
        return MatchResult(
            verdicts={pid: Verdict(pid, v["verdict"], v["case_quote"], v["provision_quote"],
                                   v["rationale"], v["model_verdict"], v["downgrade"])
                      for pid, v in r["verdicts"].items()},
            log=r["verification_log"], error=r["match_error"],
            served_model=r["served_model"], input_tokens=r["input_tokens"],
            output_tokens=r["output_tokens"])


def norm(rec):
    return json.loads(json.dumps(rec, ensure_ascii=False, default=str))


def main():
    corpus = Corpus.load(DEFAULT_OUT)
    pipeline = Pipeline(corpus.provisions, corpus.applicability_rules)
    cases = load_cases(DEFAULT_DETERMINATION_CASES)
    by_id = {c["id"]: c for c in cases}
    recs = [json.loads(l) for l in SRC.read_text(encoding="utf-8").splitlines() if l]

    def determine(case, matcher):
        return pipeline.determine(CaseFacts.model_validate(case["gold_facts"]),
                                  case["description"], relevant_date(case), matcher)

    arms = {}
    failed = []
    for r in recs:
        arm = arms.setdefault(r["arm"], ArmRuns(r["arm"]))
        if r["error"]:
            failed.append((arm, r))
            arm.runs.append(None)
            continue
        if r["arm"] == "baseline":
            continue  # handled below
        run = Run(r["case"], r["repeat"], determine(by_id[r["case"]], Replay(r)))
        if norm(run.record(r["arm"])) != norm(r):
            sys.exit(f"replay mismatch for {r['case']}#{r['repeat']}")
        arm.runs.append(run)
    print(f"replayed {sum(1 for a in arms.values() for x in a.runs if x)} runs exactly; "
          f"{len(failed)} to retry", file=sys.stderr)

    # Baseline is deterministic and had no errors: recompute it rather than replay.
    from eu_ai_check.determination.baseline import CutoffBaseline, load_tau
    from eu_ai_check.retrieval.models import SentenceTransformerEncoder
    from eu_ai_check.cli import DEFAULT_TAU
    base = CutoffBaseline(SentenceTransformerEncoder(), load_tau(DEFAULT_TAU))
    old_base = [r for r in recs if r["arm"] == "baseline"]
    arms["baseline"].runs = []
    for r in old_base:
        run = Run(r["case"], r["repeat"], determine(by_id[r["case"]], base))
        if norm(run.record("baseline")) != norm(r):
            sys.exit(f"baseline mismatch for {r['case']}")
        arms["baseline"].runs.append(run)

    live = OpenAIMatcher(openai.OpenAI(max_retries=6), effort="low")

    def one(item):
        arm, r = item
        try:
            return Run(r["case"], r["repeat"], determine(by_id[r["case"]], live))
        except Exception as exc:
            return Run(r["case"], r["repeat"], None, f"{type(exc).__name__}: {exc}")

    with ThreadPoolExecutor(max_workers=4) as pool:
        fresh = list(pool.map(one, failed))
    for (arm, r), run in zip(failed, fresh):
        i = next(k for k, x in enumerate(arm.runs) if x is None)
        arm.runs[i] = run
    still = [f"{x.case_id}#{x.repeat}: {x.error}" for x in fresh if x.error]
    print(f"retried {len(fresh)}; still failing: {still}", file=sys.stderr)

    ordered = [arms["matcher-low"], arms["baseline"]]
    scores = {a.name: score_arm(a, cases) for a in ordered}
    rule_path = score_rule_path(pipeline, cases)
    decision = decide(scores, rule_path)
    report = render(scores, rule_path, decision)
    print(report)
    OUT.with_suffix(".txt").write_text(report + "\n", encoding="utf-8")
    Path(f"{OUT}.json").write_text(json.dumps(
        {"scores": scores, "rule_path": rule_path, "decision": decision,
         "retried": [f"{r['case']}#{r['repeat']}" for _, r in failed]},
        indent=2, default=str), encoding="utf-8")
    Path(f"{OUT}.runs.jsonl").write_text("".join(
        json.dumps(x.record(a.name), ensure_ascii=False, default=str) + "\n"
        for a in ordered for x in a.runs), encoding="utf-8")
    t_in = sum(x.determination.match.input_tokens for x in fresh if x.determination and x.determination.match)
    t_out = sum(x.determination.match.output_tokens for x in fresh if x.determination and x.determination.match)
    print(f"retry cost ${t_in * 2e-6 + t_out * 10e-6:.2f}", file=sys.stderr)


if __name__ == "__main__":
    main()

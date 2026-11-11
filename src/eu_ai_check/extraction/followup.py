"""Phase 2 follow-up experiment `role-null-v1` (SYSTEM_SPEC.md §18).

Pre-registered 2026-10-02, BEFORE any follow-up API call. This module fixes in
code the metrics and the decision rule the follow-up is judged by, computed from
`.runs.jsonl` records only (no API calls):

  * the frozen baseline: first run, `data/eval/results/extraction_v1_30c.runs.jsonl`
    (commit 8ef12af), never modified;
  * the follow-up: four arms, {baseline prompt, role-null-v1} x {low, medium},
    30 cases x 3 repeats each, judge on, written to `extraction_v2_role_null_*`.
    The baseline-prompt re-run measures run-to-run noise only; the frozen run
    stays the reference.

Decision rule (as approved; it may not change after results are seen). The new
prompt counts as having worked if ALL of these hold at BOTH effort levels:

  1. total role false fills (non-null role on the cases whose role label is null)
     go down vs the frozen run, and false fills on E09 + E14 do not go up;
  2. at most 1 of the 51 guard runs (role on the 17 cases whose role label is
     non-null) becomes a miss, i.e. guard misses rise by at most 1 vs the frozen
     run, every new miss listed;
  3. no other scored field moves by more than the gap seen between the baseline
     re-run and the frozen run: for each field f != role and each effort,
     |acc(role-null-v1) - acc(frozen)| <= |acc(baseline re-run) - acc(frozen)|.
     "Moves" is taken literally: in either direction;
  4. zero conclusion leaks in the role-null-v1 arms.

E09/E14 (clear extractor errors in the baseline) are reported apart from E06/E20
(possibly ambiguous labels). With 39 runs per arm this is direction-of-effect
evidence, not statistical significance.

`posthoc_affected_persons` is separate and NOT pre-registered: a re-score of the
frozen baseline's affected_persons that also counts a value as correct when the
secondary judge accepted it. It never replaces the frozen scoring rules.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from .evaluation import ALL_SCORED

ROLE = "role_in_decision_making"
AFFECTED = "affected_persons"
CLEAR_ERROR_CASES = ("E09", "E14")
AMBIGUOUS_CASES = ("E06", "E20")
EFFORTS = ("low", "medium")
NEW_VARIANT = "role-null-v1"

#: A filled role value that states the information is missing ("human involvement
#: is not described", "no further decision process is described"). Counted
#: descriptively, on the cases whose role label is null.
ABSENCE_RE = re.compile(
    r"\bnot (?:described|specified|stated|mentioned|given|indicated)\b"
    r"|\bno (?:[\w-]+ ){0,4}(?:is|are) (?:described|specified|stated|mentioned)\b"
    r"|\bunspecified\b",
    re.IGNORECASE,
)

Records = list[dict[str, Any]]


def load_records(path: Path) -> Records:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_labels(cases_path: Path) -> dict[str, dict[str, Any]]:
    data = json.loads(cases_path.read_text(encoding="utf-8"))
    return {c["id"]: c["expected"] for c in data["cases"]}


def arm(records: Records, effort: str, variant: str | None = None) -> Records:
    """Records of one arm. The frozen run predates `prompt_variant`: a missing
    value means the baseline prompt."""
    return [
        r for r in records
        if r["effort"] == effort and (variant is None or (r.get("prompt_variant") or "baseline") == variant)
    ]


def _value(r: dict[str, Any], field: str) -> Any:
    return (r.get("raw_output") or {}).get(field)


def metrics(runs: Records, labels: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The pre-registered metrics for one arm."""
    ok = [r for r in runs if r["ok"]]
    empty_role = [r for r in ok if labels[r["case"]][ROLE] is None]
    guard = [r for r in ok if labels[r["case"]][ROLE] is not None]
    fills = [r for r in empty_role if _value(r, ROLE) is not None]
    return {
        "runs": len(runs),
        "ok": len(ok),
        "false_fills": len(fills),
        "false_fill_slots": len(empty_role),
        "false_fills_clear": sum(r["case"] in CLEAR_ERROR_CASES for r in fills),
        "false_fills_ambiguous": sum(r["case"] in AMBIGUOUS_CASES for r in fills),
        "absence_only_fills": sum(bool(ABSENCE_RE.search(_value(r, ROLE))) for r in fills),
        "guard_misses": sum(not r["correct"][ROLE] for r in guard),
        "guard_slots": len(guard),
        "field_accuracy": {
            f: statistics.fmean(r["correct"][f] for r in ok) if ok else float("nan") for f in ALL_SCORED
        },
        "affected_empty": sum(_value(r, AFFECTED) is None for r in ok),
        "leaks": sum(bool(r["leaks"]) for r in runs),
    }


def decide(frozen: dict[str, Any], replicate: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """The pre-registered decision rule, for one effort level."""
    eps = 1e-9
    moves = {}
    for f in ALL_SCORED:
        if f == ROLE:
            continue
        moved = abs(new["field_accuracy"][f] - frozen["field_accuracy"][f])
        noise = abs(replicate["field_accuracy"][f] - frozen["field_accuracy"][f])
        moves[f] = {"moved": moved, "noise": noise, "ok": moved <= noise + eps}
    checks = {
        "1_false_fills_down": new["false_fills"] < frozen["false_fills"]
        and new["false_fills_clear"] <= frozen["false_fills_clear"],
        "2_guard": new["guard_misses"] - frozen["guard_misses"] <= 1,
        "3_other_fields_within_noise": all(m["ok"] for m in moves.values()),
        "4_zero_leaks": new["leaks"] == 0,
    }
    return {"checks": checks, "field_moves": moves, "passed": all(checks.values())}


def evaluate(frozen_records: Records, followup_records: Records,
             labels: dict[str, dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {"per_effort": {}}
    for effort in EFFORTS:
        m = {
            "frozen": metrics(arm(frozen_records, effort), labels),
            "replicate": metrics(arm(followup_records, effort, "baseline"), labels),
            "new": metrics(arm(followup_records, effort, NEW_VARIANT), labels),
        }
        m["decision"] = decide(m["frozen"], m["replicate"], m["new"])
        out["per_effort"][effort] = m
    out["worked"] = all(out["per_effort"][e]["decision"]["passed"] for e in EFFORTS)
    return out


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _mark(r: dict[str, Any], field: str) -> str:
    if not r["ok"]:
        return "E"
    return "." if r["correct"][field] else "x"


def per_case_role(arms: dict[str, Records], labels: dict[str, dict[str, Any]]) -> list[str]:
    """Per case and per repeat, role_in_decision_making: E09/E14, then E06/E20,
    then the other null-label cases, then guard cases. '.' correct, 'x' miss."""
    empty = sorted(c for c, e in labels.items() if e[ROLE] is None)
    rest_empty = [c for c in empty if c not in CLEAR_ERROR_CASES + AMBIGUOUS_CASES]
    guard = sorted(c for c, e in labels.items() if e[ROLE] is not None)
    groups = [
        ("clear extractor errors in the baseline (label null)", list(CLEAR_ERROR_CASES)),
        ("possibly ambiguous labels (label null)", list(AMBIGUOUS_CASES)),
        ("other null-label cases", rest_empty),
        ("guard: role label non-null", guard),
    ]
    names = list(arms)
    lines = ["  marks per arm = repeats 0,1,2 ('.' correct, 'x' miss, 'E' failed run)",
             "  arms: " + " | ".join(f"[{i}] {n}" for i, n in enumerate(names))]
    for title, ids in groups:
        lines.append(f"  -- {title}")
        for cid in ids:
            cols = []
            for n in names:
                runs = sorted((r for r in arms[n] if r["case"] == cid), key=lambda r: r["repeat"])
                cols.append("".join(_mark(r, ROLE) for r in runs))
            lines.append(f"    {cid}  " + "  ".join(f"[{i}]{c}" for i, c in enumerate(cols)))
            # Full values wherever something was wrong, and always for the four target cases.
            if cid in CLEAR_ERROR_CASES + AMBIGUOUS_CASES or any("x" in c or "E" in c for c in cols):
                for i, n in enumerate(names):
                    for r in sorted((r for r in arms[n] if r["case"] == cid), key=lambda r: r["repeat"]):
                        lines.append(f"        [{i}]#{r['repeat']} {_mark(r, ROLE)} {_value(r, ROLE)!r}")
    return lines


def render(result: dict[str, Any], arms: dict[str, Records], labels: dict[str, dict[str, Any]]) -> str:
    lines = ["Phase 2 follow-up `role-null-v1` -- pre-registered metrics and decision rule",
             "=" * 96]
    for effort, m in result["per_effort"].items():
        lines.append(f"\n## effort={effort}")
        lines.append(f"  {'metric':44} {'frozen':>10} {'re-run':>10} {'role-null':>10}")
        rows = [
            ("runs ok", "ok", "runs"),
            ("role false fills (null-label cases)", "false_fills", "false_fill_slots"),
            ("  of which E09+E14 (clear errors)", "false_fills_clear", None),
            ("  of which E06+E20 (ambiguous labels)", "false_fills_ambiguous", None),
            ("  absence-only fills (descriptive)", "absence_only_fills", None),
            ("guard misses (role label non-null)", "guard_misses", "guard_slots"),
            ("affected_persons empty answers (descr.)", "affected_empty", "ok"),
            ("runs with leaks", "leaks", "runs"),
        ]
        for title, key, denom in rows:
            cells = [f"{m[a][key]}" + (f"/{m[a][denom]}" if denom else "") for a in ("frozen", "replicate", "new")]
            lines.append(f"  {title:44} {cells[0]:>10} {cells[1]:>10} {cells[2]:>10}")
        lines.append("  field accuracy:")
        for f in ALL_SCORED:
            acc = [m[a]["field_accuracy"][f] for a in ("frozen", "replicate", "new")]
            mv = m["decision"]["field_moves"].get(f)
            flag = "" if mv is None else ("  ok" if mv["ok"] else
                                          f"  MOVED {mv['moved']:.3f} > noise {mv['noise']:.3f}")
            lines.append(f"    {f:40} {acc[0]:>10.3f} {acc[1]:>10.3f} {acc[2]:>10.3f}{flag}")
        lines.append("  decision rule checks:")
        for k, v in m["decision"]["checks"].items():
            lines.append(f"    {k:34} {'PASS' if v else 'FAIL'}")
        lines.append(f"  effort={effort}: {'PASSED' if m['decision']['passed'] else 'NOT PASSED'}")
    lines.append("\nPRE-REGISTERED VERDICT: role-null-v1 "
                 + ("WORKED (all checks pass at both efforts)" if result["worked"]
                    else "DID NOT MEET the decision rule"))
    lines.append("\n## role_in_decision_making per case and repeat")
    lines.extend(per_case_role(arms, labels))
    return "\n".join(lines)


def posthoc_affected_persons(frozen_records: Records, labels: dict[str, dict[str, Any]]) -> str:
    """POST-HOC, not pre-registered: affected_persons in the frozen run, re-scored
    so a value also counts as correct when the secondary judge accepted it."""
    lines = [
        "POST-HOC ANALYSIS -- NOT PRE-REGISTERED. Frozen baseline (8ef12af), no API calls.",
        "affected_persons re-scored: correct = primary concept-group correct OR the",
        "secondary judge (gpt-6-astra, already in the frozen records) said same_fact.",
        "The frozen scoring rules and the frozen results are unchanged; this is analysis only.",
        "=" * 96,
    ]
    for effort in EFFORTS:
        runs = [r for r in arm(frozen_records, effort) if r["ok"]]
        kinds: dict[str, int] = defaultdict(int)
        per_case: dict[str, list[str]] = defaultdict(list)
        primary = posthoc = 0
        for r in sorted(runs, key=lambda r: (r["case"], r["repeat"])):
            label = labels[r["case"]][AFFECTED]
            value = _value(r, AFFECTED)
            judged = (r.get("judge") or {}).get(AFFECTED)
            p = r["correct"][AFFECTED]
            accepted = bool(judged and judged["same_fact"])
            primary += p
            posthoc += p or accepted
            if p:
                kind = "correct (primary)"
            elif label is None:
                kind = "value where label is null"
            elif value is None:
                kind = "empty where label has a value"
            elif accepted:
                kind = "filled, primary miss, judge accepted"
            else:
                kind = "filled, primary miss, judge rejected/absent"
            kinds[kind] += 1
            if not p:
                per_case[r["case"]].append(f"#{r['repeat']} {kind}: {value!r}")
        n = len(runs)
        lines.append(f"\n## effort={effort}  ({n} ok runs)")
        lines.append(f"  frozen primary score   {primary}/{n} = {primary / n:.3f}")
        lines.append(f"  post-hoc judge-union   {posthoc}/{n} = {posthoc / n:.3f}   (POST-HOC)")
        for k in sorted(kinds):
            lines.append(f"    {k:46} {kinds[k]}")
        for cid, items in sorted(per_case.items()):
            lines.append(f"  {cid}  label={labels[cid][AFFECTED] and labels[cid][AFFECTED]['reference']!r}")
            lines.extend(f"      {i}" for i in items)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--frozen", type=Path, required=True, help="frozen v1 .runs.jsonl")
    parser.add_argument("--followup", type=Path, help="v2 .runs.jsonl (omit for --posthoc only)")
    parser.add_argument("--out", type=Path, help="write <out>.txt and <out>.json")
    parser.add_argument("--posthoc-out", type=Path, help="write the POST-HOC affected_persons report here")
    args = parser.parse_args(argv)

    labels = load_labels(args.cases)
    frozen = load_records(args.frozen)
    if args.posthoc_out:
        args.posthoc_out.write_text(posthoc_affected_persons(frozen, labels) + "\n", encoding="utf-8")
        print(f"wrote {args.posthoc_out}")
    if args.followup:
        followup = load_records(args.followup)
        result = evaluate(frozen, followup, labels)
        arms = {f"frozen {e}": arm(frozen, e) for e in EFFORTS}
        for v in ("baseline", NEW_VARIANT):
            for e in EFFORTS:
                arms[f"{'re-run' if v == 'baseline' else v} {e}"] = arm(followup, e, v)
        report = render(result, arms, labels)
        print(report)
        if args.out:
            args.out.with_suffix(".txt").write_text(report + "\n", encoding="utf-8")
            args.out.with_suffix(".json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

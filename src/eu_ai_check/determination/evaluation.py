"""Phase 3 determination evaluation (SYSTEM_SPEC.md §18, pre-registered).

Scoring, as pre-registered:
  * a status is correct only on an exact match, including the not_applicable reason;
  * matcher-backed outputs (risk tier, prohibited practice) are scored per state
    bucket (applicable / not_applicable / unresolved); not_in_force cases are gated
    at 1.00 as deterministic outputs;
  * rule-based outputs (roles, Article 16/26) are scored on the *labelled* risk
    tier, so they test the rule code in isolation (gold inputs); the same outputs
    computed from the predicted risk tier are reported, not gated;
  * zero tolerance: an unresolved label produced as applicable / not_applicable,
    a not_in_force label produced as not_applicable, or a positive conclusion
    resting on a verdict that failed verification.
"""

from __future__ import annotations

import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Sequence

from ..extraction.schema import CaseFacts
from .combine import ANNEX_III, ARTICLE_6_1, RiskTier
from .logic import NOT_IN_FORCE, UNRESOLVED
from .pipeline import OBLIGATION_GROUPS, Determination, Matcher, Pipeline, downstream

BUCKETS = ("applicable", "not_applicable", "unresolved")
MATCHER_TARGET = 0.85
MACRO_MARGIN = 0.10
PRIMARY = "matcher-low"
BASELINE = "baseline"


def load_cases(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["cases"]


def relevant_date(case: dict) -> date:
    return date.fromisoformat(case["gold_facts"]["relevant_date"] or case["assessment_date"])


def bucket(status: str) -> str:
    return status.split("/")[0]


def labelled_tier(label: dict) -> RiskTier:
    """The labelled risk tier as route states, for the gold-input rule path."""
    status = label["status"]
    if status == NOT_IN_FORCE:
        return RiskTier(NOT_IN_FORCE)
    unknown = None if status == UNRESOLVED else False
    routes = {c: (True if c in label["system_category"] else unknown)
              for c in (ANNEX_III, ARTICLE_6_1)}
    return RiskTier(status, routes)


# --------------------------------------------------------------------------
# Running
# --------------------------------------------------------------------------

@dataclass
class Run:
    case_id: str
    repeat: int
    determination: Determination | None
    error: str | None = None

    def record(self, arm: str) -> dict[str, Any]:
        d = self.determination
        base: dict[str, Any] = {"arm": arm, "case": self.case_id, "repeat": self.repeat,
                                "error": self.error}
        if d is None:
            return base
        m = d.match
        base.update({
            "risk_tier": d.risk_tier.status,
            "system_category": d.risk_tier.system_category,
            "derogation": d.risk_tier.derogation,
            "routes": d.risk_tier.routes,
            "prohibited": d.prohibited.status,
            "prohibited_points": d.prohibited.points,
            "actor_role": d.role_label(),
            "obligations": {g: d.group_status(g) for g in OBLIGATION_GROUPS},
            "match_error": m.error if m else None,
            "verification_log": m.log if m else [],
            "served_model": m.served_model if m else None,
            "input_tokens": m.input_tokens if m else 0,
            "output_tokens": m.output_tokens if m else 0,
            "verdicts": {pid: {"verdict": v.verdict, "model_verdict": v.model_verdict,
                               "downgrade": v.downgrade_reason, "case_quote": v.case_quote,
                               "provision_quote": v.provision_quote,
                               "rationale": v.rationale}
                         for pid, v in (m.verdicts.items() if m else [])},
        })
        return base


@dataclass
class ArmRuns:
    name: str
    runs: list[Run] = field(default_factory=list)


def run_arm(name: str, matcher: Matcher, cases: Sequence[dict], repeats: int,
            pipeline: Pipeline, workers: int = 8) -> ArmRuns:
    jobs = [(c, r) for c in cases for r in range(repeats)]

    def one(job: tuple[dict, int]) -> Run:
        case, repeat = job
        try:
            det = pipeline.determine(CaseFacts.model_validate(case["gold_facts"]),
                                     case["description"], relevant_date(case), matcher)
            return Run(case["id"], repeat, det)
        except Exception as exc:  # recorded, scored as wrong, never silently dropped
            return Run(case["id"], repeat, None, f"{type(exc).__name__}: {exc}")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        runs = list(pool.map(one, jobs))
    return ArmRuns(name, runs)


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------

def _rate(hits: list[bool]) -> float | None:
    return sum(hits) / len(hits) if hits else None


def score_arm(arm: ArmRuns, cases: Sequence[dict]) -> dict[str, Any]:
    by_id = {c["id"]: c for c in cases}
    per_bucket: dict[str, dict[str, list[bool]]] = {
        k: defaultdict(list) for k in ("risk_tier", "prohibited_practice")}
    deterministic: list[bool] = []
    propagated: dict[str, list[bool]] = defaultdict(list)
    category_hits: list[bool] = []
    derogation_hits: list[bool] = []
    zero: list[str] = []
    errors = 0
    misses: list[str] = []

    for run in arm.runs:
        case = by_id[run.case_id]
        e = case["expected"]
        d = run.determination
        if d is None:
            errors += 1
        pred = {
            "risk_tier": d.risk_tier.status if d else "error",
            "prohibited_practice": d.prohibited.status if d else "error",
        }
        for key in ("risk_tier", "prohibited_practice"):
            want = e[key]["status"]
            got = pred[key]
            if want == NOT_IN_FORCE:
                deterministic.append(got == want)
                if got.startswith("not_applicable"):
                    zero.append(f"{run.case_id}#{run.repeat} {key}: not_in_force -> {got}")
            else:
                per_bucket[key][bucket(want)].append(got == want)
                if want == UNRESOLVED and bucket(got) in ("applicable", "not_applicable"):
                    zero.append(f"{run.case_id}#{run.repeat} {key}: unresolved -> {got}")
            if got != want:
                misses.append(f"{run.case_id}#{run.repeat} {key}: want {want}, got {got}")
        if d is None:
            continue
        if e["risk_tier"]["status"] == NOT_IN_FORCE:
            deterministic.append(d.role_label() == e["actor_role"])
            deterministic += [d.group_status(g) == e["obligations"][g] for g in OBLIGATION_GROUPS]
        else:
            propagated["actor_role"].append(d.role_label() == e["actor_role"])
            for g in OBLIGATION_GROUPS:
                propagated[g].append(d.group_status(g) == e["obligations"][g])
            category_hits.append(d.risk_tier.system_category == e["risk_tier"]["system_category"])
            want_derog = e["risk_tier"]["derogation"]
            if want_derog is not None:
                accepted = {want_derog, *e["risk_tier"].get("derogation_also_accepted", [])}
                derogation_hits.append(d.risk_tier.derogation in accepted)
        # Positive conclusion resting on a downgraded verdict.
        if d.match:
            positives = list(d.risk_tier.matched) + list(d.prohibited.points)
            for pid in positives:
                v = d.match.verdicts[pid]
                if v.downgrade_reason is not None:
                    zero.append(f"{run.case_id}#{run.repeat}: positive on downgraded {pid}")

    def summarise(buckets: dict[str, list[bool]]) -> dict[str, Any]:
        rates = {b: _rate(buckets.get(b, [])) for b in BUCKETS}
        present = [r for r in rates.values() if r is not None]
        return {"per_state": rates, "n": {b: len(buckets.get(b, [])) for b in BUCKETS},
                "macro": sum(present) / len(present) if present else None}

    downgrades = sum(
        1 for r in arm.runs if r.determination and r.determination.match
        for v in r.determination.match.verdicts.values() if v.downgrade_reason)
    verdict_total = sum(
        len(r.determination.match.verdicts) for r in arm.runs
        if r.determination and r.determination.match)
    return {
        "arm": arm.name,
        "runs": len(arm.runs),
        "errors": errors,
        "risk_tier": summarise(per_bucket["risk_tier"]),
        "prohibited_practice": summarise(per_bucket["prohibited_practice"]),
        "not_in_force_deterministic": _rate(deterministic),
        "propagated_rule_outputs": {k: _rate(v) for k, v in propagated.items()},
        "system_category": _rate(category_hits),
        "derogation": _rate(derogation_hits),
        "verification_downgrade_rate": (downgrades / verdict_total) if verdict_total else None,
        "zero_tolerance_violations": zero,
        "misses": misses,
    }


def score_rule_path(pipeline: Pipeline, cases: Sequence[dict]) -> dict[str, Any]:
    """Roles and Article 16/26 from the labelled risk tier: the rule code on gold
    inputs. Pre-registered target 1.00. Needs no API call."""
    failures: list[str] = []
    checks = 0
    for case in cases:
        e = case["expected"]
        tier = labelled_tier(e["risk_tier"])
        facts = CaseFacts.model_validate(case["gold_facts"])
        roles, groups = downstream(facts, relevant_date(case), tier, pipeline.engine)
        got_roles = (roles.as_label() if roles else
                     {"actor_roles": [], "unresolved_roles": [], "status": NOT_IN_FORCE})
        checks += 1
        if got_roles != e["actor_role"]:
            failures.append(f"{case['id']} roles: want {e['actor_role']}, got {got_roles}")
        for g in OBLIGATION_GROUPS:
            statuses = {s.status for s in groups[g]}
            got = statuses.pop() if len(statuses) == 1 else "mixed"
            checks += 1
            if got != e["obligations"][g]:
                failures.append(f"{case['id']} {g}: want {e['obligations'][g]}, got {got}")
    return {"checks": checks, "accuracy": 1 - len(failures) / checks, "failures": failures}


def decide(scores: dict[str, dict[str, Any]], rule_path: dict[str, Any]) -> dict[str, Any]:
    """The pre-registered decision rule (SYSTEM_SPEC.md §18): adopt M-low over B
    only if all three conditions hold."""
    m, b = scores.get(PRIMARY), scores.get(BASELINE)
    if m is None or b is None:
        return {"decided": False, "reason": "primary or baseline arm not run"}
    c1 = not m["zero_tolerance_violations"]
    c2_parts = {
        f"{det}.{bk}": (m[det]["per_state"][bk] is not None
                        and m[det]["per_state"][bk] >= MATCHER_TARGET)
        for det in ("risk_tier", "prohibited_practice") for bk in BUCKETS
    }
    c2_parts["rule_path == 1.00"] = rule_path["accuracy"] == 1.0
    c2_parts["not_in_force == 1.00"] = m["not_in_force_deterministic"] == 1.0
    c2 = all(c2_parts.values())
    c3_parts = {}
    for det in ("risk_tier", "prohibited_practice"):
        c3_parts[f"{det}.macro >= B + {MACRO_MARGIN}"] = (
            m[det]["macro"] >= b[det]["macro"] + MACRO_MARGIN)
        for bk in ("applicable", "not_applicable"):
            c3_parts[f"{det}.{bk} >= B"] = (m[det]["per_state"][bk] or 0) >= (
                b[det]["per_state"][bk] or 0)
    c3 = all(c3_parts.values())
    return {"decided": True, "adopt_matcher": c1 and c2 and c3,
            "condition_1_zero_tolerance": c1, "condition_2_targets": c2,
            "condition_2_detail": c2_parts, "condition_3_beats_baseline": c3,
            "condition_3_detail": c3_parts,
            "phase_3_acceptance_met": c1 and c2}


def _fmt(x: float | None) -> str:
    return "  -  " if x is None else f"{x:.3f}"


def render(scores: dict[str, dict[str, Any]], rule_path: dict[str, Any],
           decision: dict[str, Any]) -> str:
    lines = ["Phase 3 determination evaluation (pre-registered, SYSTEM_SPEC.md §18)", "=" * 78]
    for det in ("risk_tier", "prohibited_practice"):
        lines += ["", f"-- {det}: per-state accuracy (exact status incl. reason)",
                  f"  {'arm':<16}" + "".join(f"{b:>16}" for b in BUCKETS) + f"{'macro':>10}"]
        for name, s in scores.items():
            row = s[det]
            cells = "".join(f"{_fmt(row['per_state'][b]):>10} (n={row['n'][b]:>3})"
                            for b in BUCKETS)
            lines.append(f"  {name:<16}{cells}{_fmt(row['macro']):>10}")
    lines += ["", "-- other measures"]
    for name, s in scores.items():
        lines.append(
            f"  {name:<16} not_in_force {_fmt(s['not_in_force_deterministic'])}  "
            f"system_category {_fmt(s['system_category'])}  derogation {_fmt(s['derogation'])}  "
            f"downgrade rate {_fmt(s['verification_downgrade_rate'])}  errors {s['errors']}")
        prop = s["propagated_rule_outputs"]
        lines.append("  " + " " * 16 + "propagated (from predicted risk tier): "
                     + "  ".join(f"{k} {_fmt(v)}" for k, v in prop.items()))
    lines += ["", f"-- rule-based outputs on labelled risk tier (target 1.00): "
                  f"{rule_path['accuracy']:.3f} over {rule_path['checks']} checks"]
    lines += [f"   {f}" for f in rule_path["failures"]]
    lines += ["", "-- zero-tolerance violations"]
    for name, s in scores.items():
        lines.append(f"  {name}: {len(s['zero_tolerance_violations'])}")
        lines += [f"     {v}" for v in s["zero_tolerance_violations"]]
    lines += ["", "-- decision rule", json.dumps(decision, indent=2)]
    lines += ["", "-- misses"]
    for name, s in scores.items():
        lines += [f"  [{name}] {m}" for m in s["misses"]]
    return "\n".join(lines)

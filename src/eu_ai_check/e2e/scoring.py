"""Phase 5 scoring: gates G1-G4, attribution of every miss, and the stability report
(SYSTEM_SPEC.md §18, Phase 5 pre-registration). No API call anywhere in here.

- G1, zero tolerance (both strata), counted by root cause. One root event, such as
  one matcher verdict, is one violation, and its cascade is listed with it.
- G2, output hygiene (both strata): every case ships an answer that passes C0-C8.
  A template fallback counts as shipped. A case with no shipped answer fails G2.
- G3, determination correctness (stratum A only), exact status incl. reason:
  G3a risk tier + prohibited (n = 24) >= 19; G3b roles + Art 16/26 (n = 36) >= 33.
- G4, citation correctness (stratum A only): every correctly-statused
  applicable / not_yet_applicable / derogation-applied determination cites exactly
  the expected Annex III, Annex I and Article 5 grounds. 0 mismatches.

Every rate is reported with its 95% Wilson interval.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import date
from typing import Any, Sequence

from ..determination.evaluation import labelled_tier
from ..determination.evaluation import relevant_date as gold_relevant_date
from ..determination.logic import APPLICABLE, NA_NOT_YET, NOT_IN_FORCE, UNRESOLVED
from ..determination.pipeline import OBLIGATION_GROUPS, Pipeline, downstream
from ..determination.scopes import scope_of
from ..extraction.schema import BOOL_FIELDS, CaseFacts
from ..generation.adapter import ACTOR_ROLE, PROHIBITED, RISK_TIER

#: Pre-registered thresholds (decided 2026-10-25: dev performance minus 0.05).
G3A_N, G3A_MIN = 24, 19
G3B_N, G3B_MIN = 36, 33
G4_MAX_MISMATCHES = 0
WILSON_Z = 1.96

MATCHER_KEYS = ("risk_tier", "prohibited_practice")
RULE_KEYS = ("actor_role", *OBLIGATION_GROUPS)
KEYS = (*MATCHER_KEYS, *RULE_KEYS)


def wilson(k: int, n: int, z: float = WILSON_Z) -> tuple[float, float] | None:
    if n == 0:
        return None
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return round(max(0.0, centre - half), 3), round(min(1.0, centre + half), 3)


def _rate(k: int, n: int) -> dict[str, Any]:
    return {"k": k, "n": n, "rate": round(k / n, 4) if n else None, "wilson95": wilson(k, n)}


def bucket(status: str) -> str:
    return status.split("/")[0]


def expected_labels(case: dict) -> dict[str, Any]:
    e = case["expected"]
    return {"risk_tier": e["risk_tier"]["status"],
            "prohibited_practice": e["prohibited_practice"]["status"],
            "actor_role": e["actor_role"], **{g: e["obligations"][g] for g in OBLIGATION_GROUPS}}


# --------------------------------------------------------------------------
# Attribution replay ($0)
# --------------------------------------------------------------------------

def gold_on(case: dict) -> date:
    return gold_relevant_date(case)


def _rule_outputs(facts: CaseFacts, on: date, tier, pipeline: Pipeline) -> dict[str, Any]:
    roles, groups = downstream(facts, on, tier, pipeline.engine)
    out: dict[str, Any] = {"actor_role": roles.as_label() if roles else
                           {"actor_roles": [], "unresolved_roles": [], "status": NOT_IN_FORCE}}
    for g in OBLIGATION_GROUPS:
        statuses = {s.status for s in groups[g]}
        out[g] = statuses.pop() if len(statuses) == 1 else "mixed"
    return out


def replay_with_gold_facts(run, case: dict, pipeline: Pipeline) -> dict[str, Any]:
    """Roles and Article 16/26 from the *same* recorded matcher verdicts (hence the
    same predicted risk tier) and the gold facts."""
    return _rule_outputs(CaseFacts.model_validate(case["gold_facts"]),
                         date.fromisoformat(run.relevant_date), run.determination.risk_tier,
                         pipeline)


def replay_with_gold_tier(case: dict, pipeline: Pipeline) -> dict[str, Any]:
    """The rule path on gold inputs: labelled risk tier, gold facts, gold date."""
    return _rule_outputs(CaseFacts.model_validate(case["gold_facts"]), gold_on(case),
                         labelled_tier(case["expected"]["risk_tier"]), pipeline)


def attribute(run, case: dict, key: str, pipeline: Pipeline) -> str:
    """Exactly one stage per miss (design §4)."""
    if run.stage_failed:
        return f"stage_failure:{run.stage_failed}"
    want = expected_labels(case)[key]
    if date.fromisoformat(run.relevant_date) != gold_on(case):
        return "date_extraction"
    if key in MATCHER_KEYS:
        return "matcher"
    if replay_with_gold_facts(run, case, pipeline)[key] == want:
        return "extraction"
    if replay_with_gold_tier(case, pipeline)[key] == want:
        return "matcher"        # cascade from a wrong risk tier
    return "rule"


def root_of(case_id: str, key: str, stage: str) -> str:
    if stage == "date_extraction":
        return f"{case_id}: extracted relevant_date"
    if stage == "extraction":
        return f"{case_id}: extracted actor facts"
    if stage == "matcher":
        return f"{case_id}: matcher verdicts ({'risk_tier' if key in RULE_KEYS else key})"
    return f"{case_id}: {stage} ({key})"


# --------------------------------------------------------------------------
# Faithfulness of the shipped answer, and citation grounds
# --------------------------------------------------------------------------

def render_mismatches(run) -> list[str]:
    """G1: the shipped answer's status must be the pipeline's determination.
    Checked twice: adapter input == pipeline, and shipped rendering == input."""
    if run.determination is None or run.gen_input is None or run.generation is None:
        return []
    det, a = run.determination, run.adapter_statuses
    out = []
    if a.get(RISK_TIER) != det.risk_tier.status:
        out.append(f"risk_tier: pipeline {det.risk_tier.status}, input {a.get(RISK_TIER)}")
    if a.get(PROHIBITED) != det.prohibited.status:
        out.append(f"prohibited: pipeline {det.prohibited.status}, input {a.get(PROHIBITED)}")
    for g in OBLIGATION_GROUPS:
        if a.get(f"obligation:{g}") != det.group_status(g):
            out.append(f"{g}: pipeline {det.group_status(g)}, input {a.get(f'obligation:{g}')}")
    label = det.role_label()
    roles = {k.split(":", 1)[1]: v for k, v in a.items() if k.startswith(f"{ACTOR_ROLE}:")}
    if label["status"] == NOT_IN_FORCE:
        if any(v != NOT_IN_FORCE for v in roles.values()):
            out.append(f"actor_role: pipeline not_in_force, input {roles}")
    else:
        applicable = sorted(r for r, v in roles.items() if v == APPLICABLE)
        unresolved = sorted(r for r, v in roles.items() if v == UNRESOLVED)
        if (applicable, unresolved) != (sorted(label["actor_roles"]),
                                        sorted(label["unresolved_roles"])):
            out.append(f"actor_role: pipeline {label}, input {roles}")
    if not run.generation["status_reproduced"]:
        out.append("shipped rendering differs from its input determination")
    return out


def grounds(run) -> dict[str, list[str]]:
    d = run.risk_tier_detail
    return {"annex_iii": sorted(p for p in d["matched"] if scope_of(p) == "annex_iii"),
            "annex_i": sorted(p for p in d["matched"] if scope_of(p) == "annex_i"),
            "points": sorted(d["prohibited_points"])}


# --------------------------------------------------------------------------
# Per case
# --------------------------------------------------------------------------

def judgements(run, case: dict) -> dict[str, dict[str, Any]]:
    want = expected_labels(case)
    got = run.predicted or {}
    return {k: {"want": want[k], "got": got.get(k, "no answer"),
                "correct": run.stage_failed is None and got.get(k) == want[k]} for k in KEYS}


def g1_violations(run, case: dict, pipeline: Pipeline) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    cid = run.case_id
    for leak in run.extraction.get("leaks", []):
        out.append({"root": f"{cid}: extraction leakage",
                    "item": f"{leak['field']}: [{leak['kind']}] {leak['excerpt']}"})
    if run.determination is None:
        return out
    want, got = expected_labels(case), run.predicted

    def add(key: str, item: str) -> None:
        stage = attribute(run, case, key, pipeline)
        out.append({"root": root_of(cid, key, stage), "item": item, "stage": stage})

    for key in (*MATCHER_KEYS, *OBLIGATION_GROUPS):
        w, g = want[key], got[key]
        if bucket(w) == UNRESOLVED and bucket(g) in ("applicable", "not_applicable"):
            add(key, f"{key}: unresolved -> {g}")
        if w == NOT_IN_FORCE and g.startswith("not_applicable"):
            add(key, f"{key}: not_in_force -> {g}")
    w, g = want["actor_role"], got["actor_role"]
    for role in w["unresolved_roles"]:
        if role not in g["unresolved_roles"]:
            known = "applicable" if role in g["actor_roles"] else "not_applicable"
            add("actor_role", f"actor_role {role}: unresolved -> {known}")
    if w["status"] == NOT_IN_FORCE and g["status"] != NOT_IN_FORCE:
        add("actor_role", f"actor_role: not_in_force -> {g['status']}")
    if run.determination.match:
        positives = list(run.determination.risk_tier.matched) + list(
            run.determination.prohibited.points)
        for pid in positives:
            if run.determination.match.verdicts[pid].downgrade_reason is not None:
                out.append({"root": f"{cid}: verification ({pid})",
                            "item": f"positive conclusion on downgraded verdict {pid}"})
    for m in render_mismatches(run):
        out.append({"root": f"{cid}: generation", "item": m, "stage": "generation"})
    return out


def g2_failures(run) -> list[str]:
    if run.generation is None:
        return [f"no answer shipped ({run.stage_failed}: {run.error})"]
    g = run.generation
    if g["shipped_passed"]:
        return []
    return [f"shipped answer failed checks (fallback={g['fallback']})"]


def citation_check(run, case: dict) -> dict[str, Any] | None:
    """G4 for one stratum-A case: None if not a positive or not correctly statused."""
    rt = case["expected"]["risk_tier"]
    positive = bucket(rt["status"]) == APPLICABLE or rt["status"] == NA_NOT_YET \
        or rt["derogation"] == "applied"
    if not positive or run.stage_failed or run.predicted.get("risk_tier") != rt["status"]:
        return None
    if rt["derogation"] == "applied" and run.risk_tier_detail["derogation"] != "applied":
        return None
    got = grounds(run)
    want = {"annex_iii": sorted(rt["annex_iii"]), "annex_i": sorted(rt["annex_i"])}
    have = {"annex_iii": got["annex_iii"], "annex_i": got["annex_i"]}
    return {"case": run.case_id, "want": want, "got": have, "match": have == want}


def extraction_field_report(runs: Sequence, cases: dict[str, dict]) -> dict[str, Any]:
    """Reported only. The e2e labels are gold facts, not Phase 2 concept groups, so
    booleans and `relevant_date` are exact (null included) and free text is scored
    as null / non-null agreement."""
    hits: dict[str, list[bool]] = defaultdict(list)
    for r in runs:
        if r.facts is None:
            continue
        gold = cases[r.case_id]["gold_facts"]
        got = r.facts.model_dump(mode="json")
        for name, want in gold.items():
            if name in BOOL_FIELDS or name == "relevant_date":
                hits[name].append(got.get(name) == want)
            else:
                hits[name].append((got.get(name) is None) == (want is None))
    return {k: _rate(sum(v), len(v)) for k, v in hits.items()}


# --------------------------------------------------------------------------
# The held-out pass
# --------------------------------------------------------------------------

def _accuracy(runs: Sequence, cases: dict[str, dict], keys: Sequence[str]) -> dict[str, Any]:
    hits = [judgements(r, cases[r.case_id])[k]["correct"] for r in runs for k in keys]
    return _rate(sum(hits), len(hits))


def _per_state(runs: Sequence, cases: dict[str, dict]) -> dict[str, Any]:
    out: dict[str, dict[str, list[bool]]] = {k: defaultdict(list) for k in KEYS}
    for r in runs:
        for k, j in judgements(r, cases[r.case_id]).items():
            w = j["want"]
            state = w["status"] if k == "actor_role" else bucket(w)
            out[k][state].append(j["correct"])
    return {k: {s: _rate(sum(v), len(v)) for s, v in states.items()} for k, states in out.items()}


def score_heldout(runs: Sequence, cases: dict[str, dict], pipeline: Pipeline) -> dict[str, Any]:
    a = [r for r in runs if r.stratum == "A"]
    b = [r for r in runs if r.stratum == "B"]

    violations = [v for r in runs for v in g1_violations(r, cases[r.case_id], pipeline)]
    roots: dict[str, list[dict]] = defaultdict(list)
    for v in violations:
        roots[v["root"]].append(v)
    g2 = {r.case_id: g2_failures(r) for r in runs}
    g3a = _accuracy(a, cases, MATCHER_KEYS)
    g3b = _accuracy(a, cases, RULE_KEYS)
    cites = [c for r in a if (c := citation_check(r, cases[r.case_id])) is not None]
    mismatches = [c for c in cites if not c["match"]]

    misses = []
    for r in runs:
        for k, j in judgements(r, cases[r.case_id]).items():
            if not j["correct"]:
                misses.append({"case": r.case_id, "stratum": r.stratum, "key": k,
                               "want": j["want"], "got": j["got"],
                               "stage": attribute(r, cases[r.case_id], k, pipeline)})
    gens = [r.generation for r in runs if r.generation is not None]

    gates = {
        "G1_zero_tolerance": {"pass": not roots, "root_causes": len(roots),
                              "violations": dict(roots)},
        "G2_output_hygiene": {"pass": all(not f for f in g2.values()),
                              **_rate(sum(not f for f in g2.values()), len(g2)),
                              "failures": {k: v for k, v in g2.items() if v}},
        "G3a_risk_tier_prohibited": {"pass": g3a["n"] == G3A_N and g3a["k"] >= G3A_MIN,
                                     "threshold": f">= {G3A_MIN}/{G3A_N}", **g3a},
        "G3b_roles_obligations": {"pass": g3b["n"] == G3B_N and g3b["k"] >= G3B_MIN,
                                  "threshold": f">= {G3B_MIN}/{G3B_N}", **g3b},
        "G4_citations": {"pass": len(mismatches) <= G4_MAX_MISMATCHES,
                         "threshold": "0 mismatches",
                         **_rate(len(cites) - len(mismatches), len(cites)),
                         "mismatches": mismatches},
    }
    return {
        "phase5_acceptance_met": all(g["pass"] for g in gates.values()),
        "gates": gates,
        "stratum_A": {"cases": len(a), "per_state": _per_state(a, cases),
                      "extraction_fields": extraction_field_report(a, cases)},
        # Reported only, never pooled with stratum A, never gated on accuracy.
        "stratum_B": {"cases": len(b), "risk_tier_prohibited": _accuracy(b, cases, MATCHER_KEYS),
                      "roles_obligations": _accuracy(b, cases, RULE_KEYS),
                      "per_state": _per_state(b, cases),
                      "extraction_fields": extraction_field_report(b, cases)},
        "misses_by_stage": dict(Counter(m["stage"] for m in misses)),
        "misses": misses,
        "system_category": _rate(*_category_hits(a, cases)),
        "fallback": _rate(sum(g["fallback"] for g in gens), len(gens)),
        "stage_failures": {r.case_id: f"{r.stage_failed}: {r.error}" for r in runs
                           if r.stage_failed},
    }


def _category_hits(runs: Sequence, cases: dict[str, dict]) -> tuple[int, int]:
    hits = [r.risk_tier_detail.get("system_category")
            == cases[r.case_id]["expected"]["risk_tier"]["system_category"]
            for r in runs if r.risk_tier_detail
            and cases[r.case_id]["expected"]["risk_tier"]["status"] != NOT_IN_FORCE]
    return sum(hits), len(hits)


# --------------------------------------------------------------------------
# Stability (reported only)
# --------------------------------------------------------------------------

def score_stability(runs: Sequence, cases: dict[str, dict], pipeline: Pipeline) -> dict[str, Any]:
    """Statistics over complete rounds only. A partial round (cut by the budget or a
    smoke-test failure) is flagged and listed, and no statistic uses it."""
    rounds = Counter(r.repeat for r in runs)
    complete = sorted(rep for rep, n in rounds.items() if n == len(cases))
    partial = sorted(rep for rep, n in rounds.items() if n != len(cases))
    used = [r for r in runs if r.repeat in complete]
    by_case: dict[str, list] = defaultdict(list)
    for r in used:
        by_case[r.case_id].append(r)
    per_case = {}
    for cid, rs in by_case.items():
        statuses = {k: [r.predicted.get(k, f"stage_failed:{r.stage_failed}") for r in rs]
                    for k in KEYS}
        per_case[cid] = {
            "repeats": len(rs),
            "statuses": statuses,
            "stable": {k: len({repr(s) for s in v}) == 1 for k, v in statuses.items()},
            "correct": {k: [judgements(r, cases[cid])[k]["correct"] for r in rs] for k in KEYS},
            "g1_violations": [v for r in rs for v in g1_violations(r, cases[cid], pipeline)],
            "g2_failures": [f for r in rs for f in g2_failures(r)],
        }
    stable = [s for c in per_case.values() for s in c["stable"].values()]
    return {
        "runs": len(runs),
        "complete_rounds": len(complete),
        "partial_round": ({"repeat": partial[0], "runs_excluded_from_statistics":
                           [f"{r.case_id}#{r.repeat}" for r in runs if r.repeat in partial],
                           "note": "partial data: no conclusion is drawn from it"}
                          if partial else None),
        "determinations_stable": _rate(sum(stable), len(stable)),
        "per_case": per_case,
    }

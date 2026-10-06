"""Freeze the Phase 4 dev / held-out split (docs/phase4_design.md §6). No API call.

Written before any generator prompt exists. The 41 cases of the official Phase 3
run are split by case id, stratified on the gold risk-tier state, with a fixed
seed. D38 is forced into held-out: it is the only `not_in_force` case, and the
held-out part must contain that state. Every repeat of a case goes to the same
side. The 8 H cases are added as extra held-out.

Run once; the output is committed and SHA-pinned. Re-running must reproduce it.
"""
import hashlib
import json
import random
from collections import Counter
from pathlib import Path

CASES = Path("data/eval/determination_cases.json")
HELDOUT_H = Path("data/eval/determination_heldout_5_1_c.json")
RUN = Path("data/eval/results/determination_v1_41c_retry.runs.jsonl")
OUT = Path("data/eval/generation_split.json")

SEED = 20261006
FORCED_HELDOUT = ["D38"]
#: Held-out cases per gold risk-tier state (about one third of each).
HELDOUT_PER_STATE = {"applicable": 6, "not_applicable": 4, "unresolved": 2, "not_in_force": 1}


def sha256(path: Path) -> str:
    """Content hash with line endings normalised, so a CRLF checkout matches."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def state(status: str) -> str:
    return status.split("/")[0]


def input_states(records: list[dict]) -> Counter:
    """Determination states the generator will actually receive."""
    out: Counter = Counter()
    for r in records:
        out[state(r["risk_tier"])] += 1
        out[state(r["prohibited"])] += 1
        out[r["actor_role"]["status"].replace("known", "role_known")] += 1
        for s in r["obligations"].values():
            out[state(s)] += 1
    return out


def main() -> None:
    cases = json.loads(CASES.read_text(encoding="utf-8"))["cases"]
    h_cases = json.loads(HELDOUT_H.read_text(encoding="utf-8"))["cases"]
    records = [json.loads(l) for l in RUN.read_text(encoding="utf-8").splitlines() if l]
    records = [r for r in records if r["arm"] == "matcher-low"]

    strata: dict[str, list[str]] = {}
    for c in cases:
        strata.setdefault(state(c["expected"]["risk_tier"]["status"]), []).append(c["id"])

    rng = random.Random(SEED)
    heldout: list[str] = []
    for name in sorted(strata):
        ids = sorted(strata[name])
        forced = [i for i in ids if i in FORCED_HELDOUT]
        rest = [i for i in ids if i not in FORCED_HELDOUT]
        rng.shuffle(rest)
        heldout += forced + rest[: HELDOUT_PER_STATE[name] - len(forced)]
    heldout = sorted(heldout)
    dev = sorted(c["id"] for c in cases if c["id"] not in heldout)

    def side(ids: list[str]) -> dict:
        recs = [r for r in records if r["case"] in ids]
        return {"cases": ids, "records": len(recs),
                "input_states": dict(sorted(input_states(recs).items()))}

    split = {
        "notice": "Phase 4 generator split, frozen before any generator prompt existed. "
                  "Do not edit; regenerate with scripts/make_generation_split.py.",
        "source_run": {"path": str(RUN.as_posix()), "sha256": sha256(RUN), "arm": "matcher-low"},
        "source_cases": {"path": str(CASES.as_posix()), "sha256": sha256(CASES)},
        "method": {"seed": SEED, "stratified_on": "gold risk_tier state",
                   "heldout_per_state": HELDOUT_PER_STATE, "forced_heldout": FORCED_HELDOUT},
        "dev": side(dev),
        "heldout": side(heldout),
        "heldout_extra": {
            "path": str(HELDOUT_H.as_posix()), "sha256": sha256(HELDOUT_H),
            "cases": [c["id"] for c in h_cases],
            "cached_determinations": None,
            "note": "Never run. These cases have no Determination yet; see "
                    "docs/phase4_design.md §6 (requires a decision before use).",
        },
    }
    OUT.write_text(json.dumps(split, indent=2) + "\n", encoding="utf-8")
    for name in ("dev", "heldout"):
        print(name, split[name]["cases"], split[name]["records"], split[name]["input_states"])


if __name__ == "__main__":
    main()

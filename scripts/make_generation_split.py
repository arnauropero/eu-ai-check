"""Freeze the Phase 4 dev / held-out split (docs/phase4_design.md §6). No API call.

Written before any generator prompt exists. The 41 cases of the official Phase 3
run are split by case id, stratified on the gold risk-tier state, with a fixed
seed. D38 is forced into held-out: it is the only `not_in_force` case, and the
held-out part must contain that state. Every repeat of a case goes to the same
side.

Dev would then contain no `not_in_force` determination, so three SYNTHETIC dev
cases are added: dev cases re-dated before the Act's entry into force
(2024-08-01). `Pipeline` finds no candidate in force and never calls the matcher,
so they are deterministic and free; this script asserts both.

H01-H08 are not used in Phase 4 (user decision Q7, 2026-10-06): they stay
untouched for the Phase 5 end-to-end evaluation.

Run once; the output is committed and SHA-pinned. Re-running must reproduce it.
"""
import hashlib
import json
import random
from collections import Counter
from datetime import date
from pathlib import Path

from eu_ai_check.cli import DEFAULT_OUT
from eu_ai_check.corpus.models import Corpus
from eu_ai_check.determination.evaluation import Run
from eu_ai_check.determination.pipeline import Pipeline
from eu_ai_check.extraction.schema import CaseFacts

CASES = Path("data/eval/determination_cases.json")
HELDOUT_H = Path("data/eval/determination_heldout_5_1_c.json")
RUN = Path("data/eval/results/determination_v1_41c_retry.runs.jsonl")
OUT = Path("data/eval/generation_split.json")

SEED = 20261006
FORCED_HELDOUT = ["D38"]
#: Held-out cases per gold risk-tier state (about one third of each).
HELDOUT_PER_STATE = {"applicable": 6, "not_applicable": 4, "unresolved": 2, "not_in_force": 1}
#: (synthetic id, source dev case, relevant_date before entry into force 2024-08-01).
SYNTHETIC_DEV = [
    ("S01", "D01", "2024-07-31"),  # the day before entry into force
    ("S02", "D21", "2024-03-13"),
    ("S03", "D30", "2023-06-01"),
]


class NoMatcher:
    """Fails loudly if the pipeline ever asks for a matcher call."""
    name = "none"

    def match(self, description, candidates):
        raise AssertionError("synthetic not_in_force case reached the matcher")


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


def synthetic_records(cases: dict[str, dict], dev: list[str]) -> list[dict]:
    corpus = Corpus.load(DEFAULT_OUT)
    pipeline = Pipeline(corpus.provisions, corpus.applicability_rules)
    out = []
    for sid, source, on in SYNTHETIC_DEV:
        assert source in dev, f"{source} is not a dev case"
        case = cases[source]
        det = pipeline.determine(CaseFacts.model_validate(case["gold_facts"]),
                                 case["description"], date.fromisoformat(on), NoMatcher())
        rec = Run(sid, 0, det).record("synthetic")
        states = input_states([rec])
        assert set(states) == {"not_in_force"}, (sid, states)
        out.append({"id": sid, "synthetic": True, "source_case": source,
                    "relevant_date": on, "input_states": dict(states)})
    return out


def main() -> None:
    case_list = json.loads(CASES.read_text(encoding="utf-8"))["cases"]
    cases = {c["id"]: c for c in case_list}
    h_cases = json.loads(HELDOUT_H.read_text(encoding="utf-8"))["cases"]
    records = [json.loads(l) for l in RUN.read_text(encoding="utf-8").splitlines() if l]
    records = [r for r in records if r["arm"] == "matcher-low"]

    strata: dict[str, list[str]] = {}
    for c in case_list:
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
    dev = sorted(c["id"] for c in case_list if c["id"] not in heldout)

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
        "dev_synthetic": {
            "note": "SYNTHETIC. Not Phase 3 results: dev cases re-dated before entry into "
                    "force, replayed through Pipeline with no matcher call. One record each "
                    "(deterministic, so repeats would be identical). Used only for dev "
                    "iteration on not_in_force wording; never scored as held-out.",
            "cases": synthetic_records(cases, dev),
        },
        "heldout": side(heldout),
        "reserved_phase5": {
            "path": str(HELDOUT_H.as_posix()), "sha256": sha256(HELDOUT_H),
            "cases": [c["id"] for c in h_cases],
            "note": "Not used in Phase 4 (decision Q7, 2026-10-06). Never run; kept "
                    "untouched for the Phase 5 end-to-end evaluation.",
        },
    }
    OUT.write_text(json.dumps(split, indent=2) + "\n", encoding="utf-8")
    for name in ("dev", "heldout"):
        print(name, split[name]["cases"], split[name]["records"], split[name]["input_states"])
    for s in split["dev_synthetic"]["cases"]:
        print(s)


if __name__ == "__main__":
    main()

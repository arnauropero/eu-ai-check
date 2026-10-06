"""Freeze the Phase 4 blind pairwise rating sample (docs/phase4_design.md §5). No API call.

Written before any generation exists. Draws 20 held-out records, stratified by
record class, with a fixed public seed, at most 2 records per case. Each record
is one pair (template vs LLM answer for the same input).

The A/B assignment is drawn from a fresh secret seed and written ONLY to
`data/eval/private/generation_rating_key.json`, which is gitignored and must not
be opened until rating is finished. The committed sample file carries the key's
SHA-256, so the key is fixed now and checkable at unblinding. The key is never
overwritten: if it exists, this script refuses to run.
"""
import hashlib
import json
import random
import secrets
import sys
from collections import Counter
from pathlib import Path

SPLIT = Path("data/eval/generation_split.json")
RUN = Path("data/eval/results/determination_v1_41c_retry.runs.jsonl")
OUT = Path("data/eval/generation_rating_sample.json")
KEY = Path("data/eval/private/generation_rating_key.json")

SAMPLE_SEED = 20261007
SAMPLE_SIZE = 20
MIN_PER_CLASS = 2
MAX_PER_CASE = 2
CLASSES = ("not_in_force", "unresolved", "applicable", "not_applicable")


def state(status: str) -> str:
    return status.split("/")[0]


def record_class(r: dict) -> str:
    """not_in_force > any unresolved determination > an obligation article that
    applies (now or later) > everything not applicable."""
    if state(r["risk_tier"]) == "not_in_force":
        return "not_in_force"
    statuses = [r["risk_tier"], r["prohibited"], *r["obligations"].values()]
    if r["actor_role"]["status"] == "unresolved" or "unresolved" in map(state, statuses):
        return "unresolved"
    if any(s in ("applicable", "not_applicable/not_yet_applicable")
           for s in r["obligations"].values()):
        return "applicable"
    return "not_applicable"


def allocate(sizes: dict[str, int]) -> dict[str, int]:
    """Proportional allocation (largest remainder), at least MIN_PER_CLASS each."""
    alloc = {c: min(MIN_PER_CLASS, n) for c, n in sizes.items()}
    rest = SAMPLE_SIZE - sum(alloc.values())
    pool = {c: n - alloc[c] for c, n in sizes.items()}
    total = sum(pool.values())
    quotas = {c: rest * pool[c] / total for c in pool}
    for c in pool:
        alloc[c] += int(quotas[c])
    left = SAMPLE_SIZE - sum(alloc.values())
    for c in sorted(pool, key=lambda c: (-(quotas[c] - int(quotas[c])), c))[:left]:
        alloc[c] += 1
    return alloc


def main() -> None:
    if KEY.exists():
        sys.exit(f"{KEY} already exists; the key is never regenerated")
    split = json.loads(SPLIT.read_text(encoding="utf-8"))
    heldout = set(split["heldout"]["cases"])
    records = [json.loads(l) for l in RUN.read_text(encoding="utf-8").splitlines() if l]
    records = [r for r in records if r["arm"] == "matcher-low" and r["case"] in heldout]

    by_class: dict[str, list[dict]] = {c: [] for c in CLASSES}
    for r in sorted(records, key=lambda r: (r["case"], r["repeat"])):
        by_class[record_class(r)].append(r)
    sizes = {c: len(v) for c, v in by_class.items() if v}
    alloc = allocate(sizes)

    rng = random.Random(SAMPLE_SEED)
    per_case: Counter = Counter()
    sample = []
    for c in CLASSES:
        if c not in alloc:
            continue
        pool = list(by_class[c])
        rng.shuffle(pool)
        taken = 0
        for r in pool:
            if taken == alloc[c]:
                break
            if per_case[r["case"]] >= MAX_PER_CASE:
                continue
            per_case[r["case"]] += 1
            taken += 1
            sample.append({"class": c, "record": f"{r['case']}#{r['repeat']}"})
        assert taken == alloc[c], (c, taken, alloc[c])
    for i, s in enumerate(sample, 1):
        s["sample_id"] = f"R{i:02d}"

    key_seed = secrets.randbits(64)
    key_rng = random.Random(key_seed)
    key = {
        "notice": "SECRET until rating is finished. A/B assignment per sample_id.",
        "seed": key_seed,
        "assignment": {s["sample_id"]: {"A": a, "B": b} for s in sample
                       for a, b in [key_rng.choice([("template", "llm"), ("llm", "template")])]},
    }
    KEY.parent.mkdir(parents=True, exist_ok=True)
    key_text = json.dumps(key, indent=2, sort_keys=True) + "\n"
    KEY.write_text(key_text, encoding="utf-8")

    out = {
        "notice": "Phase 4 blind pairwise rating sample, frozen before any generation "
                  "existed. Do not edit. The A/B key is in a separate gitignored file.",
        "method": {"seed": SAMPLE_SEED, "size": SAMPLE_SIZE, "min_per_class": MIN_PER_CLASS,
                   "max_per_case": MAX_PER_CASE, "population": "held-out records",
                   "class_sizes": sizes, "allocation": alloc},
        "samples": sample,
        "key": {"path": str(KEY.as_posix()),
                "sha256": hashlib.sha256(key_text.encode("utf-8")).hexdigest()},
    }
    OUT.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print("class sizes", sizes, "allocation", alloc)
    print("records", [s["record"] for s in sample])
    print("key written (not shown); sha256", out["key"]["sha256"])


if __name__ == "__main__":
    main()

"""Blind pairwise rating: sheet generation and, after rating, scoring (§18).

The A/B key (data/eval/private/generation_rating_key.json) is opened ONLY in this
module, and never printed, logged or returned:
- `make_sheet` runs only once the held-out generation exists. It checks the key
  against the SHA-256 committed in the frozen sample file, then writes a blinded
  sheet (answers labelled A/B, no arm names) and an empty ratings file;
- `score` runs only once every sample has a preference. It then reads the key,
  checks it again, and counts LLM wins (ties go to the template).

Rater (§18 deviation, 2026-10-06): an LLM rater rates all 20 pairs, and results are
labelled "LLM-rated (Claude)". The user's ratings of a 6-pair validation sample are
compared with it, reported only.

Blinding is partial: the template has no model sentences, so style can reveal the
arm. That limitation is recorded in the pre-registration.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

SAMPLE = Path("data/eval/generation_rating_sample.json")
HELDOUT_RUNS = Path("data/eval/results/generation_heldout.runs.jsonl")
SHEET = Path("data/eval/results/generation_rating_sheet.md")
RATINGS = Path("data/eval/results/generation_ratings.json")
#: Validation sample (6 informative pairs, seed 20261008) rated by the user; the full
#: 20 are rated by an LLM rater (§18 deviation note, 2026-10-06, before any rating).
VALIDATION = Path("data/eval/generation_rating_validation.json")
VALIDATION_RATINGS = Path("data/eval/results/generation_ratings_validation.json")
RATER_LABEL = "LLM-rated (Claude)"
PREFERENCES = ("A", "B", "tie")


class RatingError(RuntimeError):
    pass


def _load_key(sample: dict, key_path: Path | None = None) -> dict:
    """Read and verify the sealed key. The content never leaves this module."""
    path = key_path or Path(sample["key"]["path"])
    if not path.exists():
        raise RatingError(f"rating key not found at {path}")
    data = path.read_bytes().replace(b"\r\n", b"\n")
    if hashlib.sha256(data).hexdigest() != sample["key"]["sha256"]:
        raise RatingError("rating key does not match its committed SHA-256")
    return json.loads(data)["assignment"]


def verify_key_commitment(sample_path: Path = SAMPLE, key_path: Path | None = None) -> bool:
    """True iff the key is present and matches its commitment. Reveals nothing else."""
    sample = json.loads(sample_path.read_text(encoding="utf-8"))
    try:
        _load_key(sample, key_path)
    except RatingError:
        return False
    return True


def make_sheet(*, sample_path: Path = SAMPLE, runs_path: Path = HELDOUT_RUNS,
               sheet_path: Path = SHEET, ratings_path: Path = RATINGS,
               key_path: Path | None = None) -> dict[str, Any]:
    if not runs_path.exists():
        raise RatingError("no held-out generation yet: the sheet is built only after it")
    if ratings_path.exists():
        raise RatingError(f"{ratings_path} already exists; not overwriting ratings")
    sample = json.loads(sample_path.read_text(encoding="utf-8"))
    rows = {r["ref"]: r for r in (json.loads(l) for l in
                                  runs_path.read_text(encoding="utf-8").splitlines() if l)}
    assignment = _load_key(sample, key_path)

    lines = ["# Phase 4 blind pairwise rating", "",
             "For each pair, record in the ratings file which answer better tells the user "
             "what applies to them, why, and what is missing, while staying faithful to the "
             "cited text: \"A\", \"B\" or \"tie\". Also record whether each answer is faithful "
             "to its cited provisions (true/false). Do not open the key until every pair is "
             "rated.", ""]
    for s in sample["samples"]:
        row = rows.get(s["record"])
        if row is None:
            raise RatingError(f"held-out runs have no record {s['record']}")
        texts = {"template": row["template_text"], "llm": row["shipped_text"]}
        sides = assignment[s["sample_id"]]
        lines += [f"## {s['sample_id']}", "", "### Answer A", "", texts[sides["A"]], "",
                  "### Answer B", "", texts[sides["B"]], ""]
    sheet_path.parent.mkdir(parents=True, exist_ok=True)
    sheet_path.write_text("\n".join(lines), encoding="utf-8")
    blank = {s["sample_id"]: {"preference": None, "faithful_A": None, "faithful_B": None,
                              "note": ""} for s in sample["samples"]}
    ratings_path.write_text(json.dumps(blank, indent=2) + "\n", encoding="utf-8")
    return {"sheet": str(sheet_path), "ratings": str(ratings_path), "pairs": len(blank)}


def _check_ratings(ratings: dict, ids: list[str], what: str) -> None:
    if sorted(ratings) != sorted(ids):
        raise RatingError(f"{what} do not cover exactly the frozen sample")
    for sid in ids:
        r = ratings[sid]
        if r.get("preference") not in PREFERENCES:
            raise RatingError(f"{sid}: preference must be one of {PREFERENCES} before unblinding")
        if not isinstance(r.get("faithful_A"), bool) or not isinstance(r.get("faithful_B"), bool):
            raise RatingError(f"{sid}: faithful_A / faithful_B must be true or false")


def agreement(main: dict, validation: dict, ids: list[str]) -> dict[str, Any]:
    """Reported only: how often the user's validation ratings equal the LLM rater's
    on the same pairs. Compares A/B/tie labels, so it needs no key."""
    same = {f: sum(main[s][f] == validation[s][f] for s in ids)
            for f in ("preference", "faithful_A", "faithful_B")}
    n = len(ids)
    return {"reported_only": True, "pairs": n,
            "preference_agreement": f"{same['preference']}/{n}",
            "preference_agreement_rate": round(same["preference"] / n, 4) if n else None,
            "faithful_A_agreement": f"{same['faithful_A']}/{n}",
            "faithful_B_agreement": f"{same['faithful_B']}/{n}",
            "per_pair": {s: {"user": validation[s]["preference"], "llm": main[s]["preference"]}
                         for s in ids}}


def score(*, sample_path: Path = SAMPLE, ratings_path: Path = RATINGS,
          key_path: Path | None = None, validation_path: Path | None = VALIDATION,
          validation_ratings_path: Path | None = VALIDATION_RATINGS) -> dict[str, Any]:
    sample = json.loads(sample_path.read_text(encoding="utf-8"))
    ratings = json.loads(ratings_path.read_text(encoding="utf-8"))
    ids = [s["sample_id"] for s in sample["samples"]]
    _check_ratings(ratings, ids, "ratings")
    # The validation ratings are checked before the key is opened, like the main ones.
    validation: dict[str, Any] = {"reported_only": True, "status": "not provided"}
    if validation_path and validation_ratings_path and validation_ratings_path.exists():
        vids = json.loads(validation_path.read_text(encoding="utf-8"))["sample_ids"]
        vratings = json.loads(validation_ratings_path.read_text(encoding="utf-8"))
        if all(r.get("preference") is None for r in vratings.values()):
            validation = {"reported_only": True, "status": "not rated"}
        else:  # partly filled is an error, never a silent subset
            _check_ratings(vratings, vids, "validation ratings")
            validation = agreement(ratings, vratings, vids)
    assignment = _load_key(sample, key_path)  # opened only now: every pair is rated
    llm_wins = template_wins = ties = 0
    faithful = {"llm": 0, "template": 0}
    for sid in ids:
        sides, r = assignment[sid], ratings[sid]
        if r["preference"] == "tie":
            ties += 1
        elif sides[r["preference"]] == "llm":
            llm_wins += 1
        else:
            template_wins += 1
        faithful[sides["A"]] += r["faithful_A"]
        faithful[sides["B"]] += r["faithful_B"]
    # The not_in_force pairs carry no evidence, so both arms render the same text and
    # they tie by construction. The pre-registered bar stays 15/20 (pairwise_ok); the
    # same wins are also reported over the informative pairs only (design note).
    identical = {s["sample_id"] for s in sample["samples"] if s["class"] == "not_in_force"}
    informative_wins = sum(1 for sid in ids if sid not in identical
                           and ratings[sid]["preference"] != "tie"
                           and assignment[sid][ratings[sid]["preference"]] == "llm")
    return {"rater": RATER_LABEL, "pairs": len(ids), "llm_wins": llm_wins, "template_wins": template_wins,
            "ties": ties, "pairwise_ok": llm_wins >= 15,
            "llm_wins_of_20": f"{llm_wins}/{len(ids)}",
            "informative_pairs": len(ids) - len(identical),
            "llm_wins_of_informative": f"{informative_wins}/{len(ids) - len(identical)}",
            "identical_pairs_rated_tie": sum(ratings[sid]["preference"] == "tie"
                                             for sid in identical),
            "faithful_llm": faithful["llm"], "faithful_template": faithful["template"],
            # Reported only: never read by the adoption rule.
            "validation_agreement": validation}

"""Generation inputs for each side of the frozen split (data/eval/generation_split.json).

Every input is built from cached Phase 3 records (or the synthetic not_in_force
replays) at zero cost. Gold labels are returned separately, for scoring only, and
never enter a `GenerationInput`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ..corpus.models import Corpus
from ..determination.evaluation import relevant_date
from ..determination.pipeline import Pipeline
from ..extraction.schema import CaseFacts
from .adapter import GenerationInput, adapt, replay, replay_synthetic

SPLIT = Path("data/eval/generation_split.json")
CORPUS = Path("data/corpus")
SIDES = ("dev", "heldout")


@dataclass(frozen=True)
class Item:
    ref: str                     # "D10#0", or "S01" for a synthetic case
    input: GenerationInput
    synthetic: bool
    gold_differs: bool           # some input determination differs from the gold label


class Inputs:
    def __init__(self, split_path: Path = SPLIT, corpus_dir: Path = CORPUS):
        self.split = json.loads(split_path.read_text(encoding="utf-8"))
        self.corpus = Corpus.load(corpus_dir)
        self.pipeline = Pipeline(self.corpus.provisions, self.corpus.applicability_rules)
        cases_path = Path(self.split["source_cases"]["path"])
        self.cases = {c["id"]: c for c in
                      json.loads(cases_path.read_text(encoding="utf-8"))["cases"]}
        run_path = Path(self.split["source_run"]["path"])
        recs = [json.loads(l) for l in run_path.read_text(encoding="utf-8").splitlines() if l]
        self.records = {f"{r['case']}#{r['repeat']}": r for r in recs
                        if r["arm"] == self.split["source_run"]["arm"]}

    def corpus_texts(self) -> dict[str, str]:
        return {p.versioned_provision_id: p.text for p in self.corpus.provisions}

    def _gold_differs(self, record: dict) -> bool:
        e = self.cases[record["case"]]["expected"]
        return (record["risk_tier"] != e["risk_tier"]["status"]
                or record["prohibited"] != e["prohibited_practice"]["status"]
                or record["actor_role"] != e["actor_role"]
                or record["obligations"] != e["obligations"])

    def record_item(self, ref: str) -> Item:
        record = self.records[ref]
        case = self.cases[record["case"]]
        on = relevant_date(case)
        det = replay(self.pipeline, record, case, on)
        inp = adapt(det, CaseFacts.model_validate(case["gold_facts"]), on, self.corpus,
                    self.pipeline, ref)
        return Item(ref, inp, False, self._gold_differs(record))

    def synthetic_items(self) -> list[Item]:
        out = []
        for s in self.split["dev_synthetic"]["cases"]:
            case = self.cases[s["source_case"]]
            on = date.fromisoformat(s["relevant_date"])
            det = replay_synthetic(self.pipeline, case, on)
            inp = adapt(det, CaseFacts.model_validate(case["gold_facts"]), on, self.corpus,
                        self.pipeline, s["id"])
            out.append(Item(s["id"], inp, True, False))
        return out

    def side(self, name: str, repeats: tuple[int, ...] | None = None,
             synthetic: bool = True) -> list[Item]:
        """Dev: by default every repeat plus S01-S03. Held-out: every repeat,
        no synthetic cases. `repeats=(0,)` gives the dev iteration slice."""
        if name not in SIDES:
            raise ValueError(f"unknown side {name!r}")
        cases = set(self.split[name]["cases"])
        refs = sorted(r for r, rec in self.records.items() if rec["case"] in cases
                      and (repeats is None or rec["repeat"] in repeats))
        items = [self.record_item(r) for r in refs]
        if name == "dev" and synthetic:
            items += self.synthetic_items()
        return items

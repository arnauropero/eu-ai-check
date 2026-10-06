"""The Phase 4 dev / held-out split and rating sample are frozen (docs/phase4_design.md).

Both were committed before any generator prompt or generation existed. These tests
pin their content, check that they still describe the official Phase 3 run they
were cut from, and check the properties each was required to have.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import pytest

from eu_ai_check.corpus.models import Corpus
from eu_ai_check.determination.evaluation import Run
from eu_ai_check.determination.pipeline import Pipeline
from eu_ai_check.extraction.schema import CaseFacts

SPLIT = json.loads(Path("data/eval/generation_split.json").read_text(encoding="utf-8"))
SAMPLE = json.loads(Path("data/eval/generation_rating_sample.json").read_text(encoding="utf-8"))

#: SHA-256 of the split's content, frozen in the Phase 4 pre-registration (2026-10-06).
FROZEN_SPLIT_SHA = "8300d7cc71658ce2fbaad7202363878be74afc6c32d477184cc0f436b519eb28"
#: SHA-256 of the rating sample's content, frozen in the same pre-registration.
FROZEN_SAMPLE_SHA = "4ca0d547431ae1ae7cecf172648ea3988b4a38d1a4ca1035674ac256f32d4509"


def _content_sha(obj: dict) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False)
                          .encode("utf-8")).hexdigest()


def _file_sha(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _heldout_records() -> dict[str, dict]:
    lines = Path(SPLIT["source_run"]["path"]).read_text(encoding="utf-8").splitlines()
    recs = [json.loads(l) for l in lines if l]
    return {f"{r['case']}#{r['repeat']}": r for r in recs
            if r["arm"] == "matcher-low" and r["case"] in SPLIT["heldout"]["cases"]}


def test_split_is_frozen() -> None:
    assert _content_sha(SPLIT) == FROZEN_SPLIT_SHA


def test_sources_are_unchanged() -> None:
    for source in (SPLIT["source_run"], SPLIT["source_cases"], SPLIT["reserved_phase5"]):
        assert _file_sha(source["path"]) == source["sha256"], source["path"]


def test_split_partitions_the_official_run_by_case() -> None:
    cases = json.loads(Path(SPLIT["source_cases"]["path"]).read_text(encoding="utf-8"))["cases"]
    dev, heldout = set(SPLIT["dev"]["cases"]), set(SPLIT["heldout"]["cases"])
    assert not dev & heldout
    assert dev | heldout == {c["id"] for c in cases}
    assert (len(dev), len(heldout)) == (28, 13)
    assert SPLIT["dev"]["records"] + SPLIT["heldout"]["records"] == 123


def test_heldout_contains_the_required_states() -> None:
    states = SPLIT["heldout"]["input_states"]
    for state in ("applicable", "unresolved", "not_in_force"):
        assert states.get(state, 0) > 0, state


def test_h_cases_are_reserved_for_phase_5_not_used() -> None:
    assert SPLIT["reserved_phase5"]["cases"] == [f"H0{i}" for i in range(1, 9)]
    used = set(SPLIT["dev"]["cases"]) | set(SPLIT["heldout"]["cases"])
    assert not used & set(SPLIT["reserved_phase5"]["cases"])


class _NoMatcher:
    name = "none"

    def match(self, description, candidates):
        raise AssertionError("a synthetic not_in_force case reached the matcher")


def test_synthetic_dev_cases_are_not_in_force_without_a_matcher_call() -> None:
    synthetic = SPLIT["dev_synthetic"]["cases"]
    assert [s["id"] for s in synthetic] == ["S01", "S02", "S03"]
    corpus = Corpus.load(Path("data/corpus"))
    pipeline = Pipeline(corpus.provisions, corpus.applicability_rules)
    cases = {c["id"]: c for c in json.loads(
        Path(SPLIT["source_cases"]["path"]).read_text(encoding="utf-8"))["cases"]}
    for s in synthetic:
        assert s["synthetic"] is True
        assert s["source_case"] in SPLIT["dev"]["cases"]
        on = date.fromisoformat(s["relevant_date"])
        assert on < date(2024, 8, 1)
        case = cases[s["source_case"]]
        det = pipeline.determine(CaseFacts.model_validate(case["gold_facts"]),
                                 case["description"], on, _NoMatcher())
        rec = Run(s["id"], 0, det).record("synthetic")
        assert rec["risk_tier"] == rec["prohibited"] == "not_in_force"
        assert rec["actor_role"]["status"] == "not_in_force"
        assert set(rec["obligations"].values()) == {"not_in_force"}
        assert rec["verdicts"] == {}


def test_rating_sample_is_frozen() -> None:
    assert _content_sha(SAMPLE) == FROZEN_SAMPLE_SHA


def test_rating_sample_is_20_heldout_records_stratified() -> None:
    records = _heldout_records()
    picked = [s["record"] for s in SAMPLE["samples"]]
    assert len(picked) == len(set(picked)) == 20
    assert set(picked) <= set(records)
    per_case: dict[str, int] = {}
    for p in picked:
        per_case[p.split("#")[0]] = per_case.get(p.split("#")[0], 0) + 1
    assert max(per_case.values()) <= SAMPLE["method"]["max_per_case"]
    classes = {s["class"] for s in SAMPLE["samples"]}
    assert classes == {"not_in_force", "unresolved", "applicable", "not_applicable"}


def test_rating_key_matches_its_commitment_when_present() -> None:
    """The key is gitignored and secret until rating ends. When it is present
    (the machine that froze it), it must still match the committed hash. The test
    checks the hash only and never prints the key."""
    key = Path(SAMPLE["key"]["path"])
    if not key.exists():
        pytest.skip("rating key not present on this machine")
    digest = hashlib.sha256(key.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    assert digest == SAMPLE["key"]["sha256"]

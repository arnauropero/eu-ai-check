"""The Phase 4 dev / held-out split is frozen (docs/phase4_design.md §6).

It was committed before any generator prompt existed. These tests pin its content,
check that it still describes the official Phase 3 run it was cut from, and check
the properties the split was required to have.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

SPLIT_PATH = Path("data/eval/generation_split.json")
SPLIT = json.loads(SPLIT_PATH.read_text(encoding="utf-8"))

#: SHA-256 of the split's content as frozen on 2026-10-06.
FROZEN_SPLIT_SHA = "3bc745169ce9e3056a41df3b56c980d8067651f3e8ca2f9edfefc8cfdb98bbc8"


def _file_sha(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def test_split_is_frozen() -> None:
    digest = hashlib.sha256(
        json.dumps(SPLIT, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    assert digest == FROZEN_SPLIT_SHA


def test_sources_are_unchanged() -> None:
    for source in (SPLIT["source_run"], SPLIT["source_cases"], SPLIT["heldout_extra"]):
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
    assert SPLIT["heldout_extra"]["cases"] == [f"H0{i}" for i in range(1, 9)]

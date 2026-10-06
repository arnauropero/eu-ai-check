"""System-language rate: reported only, lexicon pinned, dev pass 1 baseline."""

from __future__ import annotations

import json
from pathlib import Path

from eu_ai_check.generation.inputs import Inputs
from eu_ai_check.generation.system_language import (
    SYSTEM_LANGUAGE_LEXICON,
    claims_of,
    lexicon_sha,
    phrases,
    system_language,
)

PINNED_SHA = "96ad5e7670a299c4ab983c3721e500216b111bad3386c1e9858f76d1d04a197e"


def test_lexicon_is_pinned() -> None:
    assert lexicon_sha() == PINNED_SHA, "the system-language lexicon changed"
    assert len(SYSTEM_LANGUAGE_LEXICON) == 9


def test_legal_uses_do_not_count() -> None:
    for text in ("Article 16(f) requires the relevant conformity assessment.",
                 "Deployers use the supplied information for a data protection impact assessment.",
                 "Article 26(4) requires relevant input data.",
                 "Annex III(6)(c) covers evaluation of the reliability of evidence."):
        assert phrases(text) == [], text


def test_system_phrasings_count() -> None:
    text = ("The code recorded this provision as matched. The supplied evidence has no "
            "excerpts. The assessment checked the cited points.")
    found = {k for k, _ in phrases(text)}
    assert found == {"code", "records/recorded", "match/matched/matching", "supplied evidence",
                     "excerpt(s)", "the cited", "system-side assessment"}


def test_dev_pass_1_baseline() -> None:
    rows = [json.loads(l) for l in Path("data/eval/results/generation_dev_pass1.runs.jsonl")
            .read_text(encoding="utf-8").splitlines() if l]
    m = system_language(claims_of(rows))
    assert (m["claims_affected"], m["claims_total"]) == (70, 179)
    assert m["occurrences"]["code"] == 67 and sum(m["occurrences"].values()) == 152


def test_adapter_analysis_strings_carry_no_system_language() -> None:
    inputs = Inputs()
    for item in inputs.side("dev", repeats=None, synthetic=False) + inputs.synthetic_items():
        for d in item.input.as_prompt_data()["determinations"]:
            assert phrases(d["analysis"]) == [], (item.ref, d["analysis"])
            assert all(set(e) == {"evidence_id", "citation", "text"} for e in d["evidence"])

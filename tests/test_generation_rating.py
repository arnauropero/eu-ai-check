"""Rating sheet and scoring, on temporary files with a test-only key.

The real sealed key is touched only through `rating.verify_key_commitment` (in
test_generation_split.py), which returns a bool and reveals nothing.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from eu_ai_check.generation import rating
from eu_ai_check.generation.rating import RatingError, make_sheet, score

SAMPLE_IDS = ["R01", "R02", "R03"]


@pytest.fixture
def files(tmp_path):
    key = {"notice": "test", "seed": 1, "assignment": {
        "R01": {"A": "template", "B": "llm"},
        "R02": {"A": "llm", "B": "template"},
        "R03": {"A": "template", "B": "llm"}}}
    key_text = json.dumps(key, indent=2, sort_keys=True) + "\n"
    key_path = tmp_path / "key.json"
    key_path.write_text(key_text, encoding="utf-8")
    sample = {"samples": [{"sample_id": s, "record": f"D0{i}#0", "class": "x"}
                          for i, s in enumerate(SAMPLE_IDS, 1)],
              "key": {"path": str(key_path),
                      "sha256": hashlib.sha256(key_text.encode()).hexdigest()}}
    sample_path = tmp_path / "sample.json"
    sample_path.write_text(json.dumps(sample), encoding="utf-8")
    runs = tmp_path / "heldout.runs.jsonl"
    runs.write_text("".join(json.dumps({"ref": f"D0{i}#0",
                                        "template_text": f"TEMPLATE-{i}",
                                        "shipped_text": f"LLM-{i}"}) + "\n"
                            for i in range(1, 4)), encoding="utf-8")
    return {"sample_path": sample_path, "runs_path": runs, "key_path": key_path,
            "sheet_path": tmp_path / "sheet.md", "ratings_path": tmp_path / "ratings.json"}


def sheet_kwargs(f):
    return {k: f[k] for k in ("sample_path", "runs_path", "sheet_path", "ratings_path", "key_path")}


def test_sheet_requires_heldout_generation(files) -> None:
    files["runs_path"].unlink()
    with pytest.raises(RatingError, match="no held-out generation"):
        make_sheet(**sheet_kwargs(files))


def test_sheet_is_blinded_and_reveals_nothing(files, capsys) -> None:
    out = make_sheet(**sheet_kwargs(files))
    sheet = files["sheet_path"].read_text(encoding="utf-8")
    # R01: A=template, B=llm ; R02: A=llm, B=template
    r01, r02 = sheet.split("## R01")[1].split("## R02")
    assert r01.index("TEMPLATE-1") < r01.index("LLM-1")
    assert r02.index("LLM-2") < r02.index("TEMPLATE-2")
    lowered = sheet.lower()
    assert "template" not in lowered.replace("template-", "") and '"llm"' not in lowered
    key_text = files["key_path"].read_text(encoding="utf-8")
    captured = capsys.readouterr()
    for secret in ('"assignment"', '"seed"'):
        assert secret not in captured.out + captured.err + json.dumps(out) + sheet
    assert key_text not in sheet
    blank = json.loads(files["ratings_path"].read_text(encoding="utf-8"))
    assert sorted(blank) == SAMPLE_IDS and blank["R01"]["preference"] is None


def test_sheet_refuses_a_tampered_key(files) -> None:
    files["key_path"].write_text("{}", encoding="utf-8")
    with pytest.raises(RatingError, match="does not match"):
        make_sheet(**sheet_kwargs(files))


def test_sheet_never_overwrites_ratings(files) -> None:
    make_sheet(**sheet_kwargs(files))
    with pytest.raises(RatingError, match="already exists"):
        make_sheet(**sheet_kwargs(files))


def test_score_refuses_to_unblind_before_rating_is_complete(files) -> None:
    make_sheet(**sheet_kwargs(files))
    with pytest.raises(RatingError, match="before unblinding"):
        score(sample_path=files["sample_path"], ratings_path=files["ratings_path"],
              key_path=files["key_path"], validation_ratings_path=None)


def test_score_counts_wins_with_ties_to_the_template(files) -> None:
    make_sheet(**sheet_kwargs(files))
    ratings = {"R01": {"preference": "B", "faithful_A": True, "faithful_B": True, "note": ""},
               "R02": {"preference": "B", "faithful_A": False, "faithful_B": True, "note": ""},
               "R03": {"preference": "tie", "faithful_A": True, "faithful_B": True, "note": ""}}
    files["ratings_path"].write_text(json.dumps(ratings), encoding="utf-8")
    out = score(sample_path=files["sample_path"], ratings_path=files["ratings_path"],
                key_path=files["key_path"], validation_ratings_path=None)
    assert (out["llm_wins"], out["template_wins"], out["ties"]) == (1, 1, 1)
    assert out["faithful_llm"] == 2 and out["faithful_template"] == 3
    assert out["pairwise_ok"] is False


def test_verify_key_commitment_reveals_only_a_bool(files) -> None:
    assert rating.verify_key_commitment(files["sample_path"], files["key_path"]) is True
    files["key_path"].write_text("tampered", encoding="utf-8")
    assert rating.verify_key_commitment(files["sample_path"], files["key_path"]) is False


# ---------------------------------------------------------------------------
# CLI wiring ($0 modes only; API modes are shown to be blocked by the guard)
# ---------------------------------------------------------------------------

def test_cli_template_mode_runs_at_zero_cost(capsys) -> None:
    from eu_ai_check.cli import main
    assert main(["eval-generation", "--mode", "template"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["heldout"]["failed"] == 0 and out["heldout"]["records"] == 39


def test_cli_rating_sheet_refuses_before_heldout(monkeypatch, tmp_path) -> None:
    from eu_ai_check.cli import main
    monkeypatch.setattr(rating, "HELDOUT_RUNS", tmp_path / "missing.jsonl")
    monkeypatch.setattr(rating.make_sheet, "__kwdefaults__",
                        {**rating.make_sheet.__kwdefaults__, "runs_path": tmp_path / "missing.jsonl"})
    with pytest.raises(RatingError, match="no held-out generation"):
        main(["eval-generation", "--mode", "rating-sheet"])


@pytest.mark.expects_client_attempt
@pytest.mark.parametrize("mode", ["smoke", "dev", "heldout"])
def test_cli_api_modes_need_a_real_client_which_tests_cannot_get(mode) -> None:
    from conftest import RealClientInstantiated

    from eu_ai_check.cli import main
    with pytest.raises(RealClientInstantiated):
        main(["eval-generation", "--mode", mode])


def test_score_reports_wins_over_all_and_over_informative_pairs(files) -> None:
    """not_in_force pairs are identical in both arms; scoring reports x/20 and x/18."""
    sample = json.loads(files["sample_path"].read_text(encoding="utf-8"))
    sample["samples"][2]["class"] = "not_in_force"          # R03
    files["sample_path"].write_text(json.dumps(sample), encoding="utf-8")
    make_sheet(**sheet_kwargs(files))
    ratings = {"R01": {"preference": "B", "faithful_A": True, "faithful_B": True, "note": ""},
               "R02": {"preference": "A", "faithful_A": True, "faithful_B": True, "note": ""},
               "R03": {"preference": "tie", "faithful_A": True, "faithful_B": True, "note": ""}}
    files["ratings_path"].write_text(json.dumps(ratings), encoding="utf-8")
    out = score(sample_path=files["sample_path"], ratings_path=files["ratings_path"],
                key_path=files["key_path"], validation_ratings_path=None)
    assert out["llm_wins_of_20"] == "2/3" and out["llm_wins_of_informative"] == "2/2"
    assert (out["informative_pairs"], out["identical_pairs_rated_tie"]) == (2, 1)
    assert out["pairwise_ok"] is False          # the pre-registered bar is unchanged


def test_frozen_sample_has_exactly_two_identical_not_in_force_pairs() -> None:
    sample = json.loads(Path("data/eval/generation_rating_sample.json").read_text(encoding="utf-8"))
    nif = [s["record"] for s in sample["samples"] if s["class"] == "not_in_force"]
    assert len(sample["samples"]) == 20 and sorted(nif) == ["D38#1", "D38#2"]


def _rated(prefs):
    return {sid: {"preference": p, "faithful_A": True, "faithful_B": True, "note": ""}
            for sid, p in prefs.items()}


def test_validation_agreement_is_reported_only(files, tmp_path) -> None:
    make_sheet(**sheet_kwargs(files))
    main = _rated({"R01": "B", "R02": "B", "R03": "tie"})
    files["ratings_path"].write_text(json.dumps(main), encoding="utf-8")
    vpath = tmp_path / "validation.json"
    vpath.write_text(json.dumps({"sample_ids": ["R01", "R03"]}), encoding="utf-8")
    vr = tmp_path / "validation_ratings.json"
    kw = {"sample_path": files["sample_path"], "ratings_path": files["ratings_path"],
          "key_path": files["key_path"], "validation_path": vpath,
          "validation_ratings_path": vr}
    base = score(**{**kw, "validation_ratings_path": None})
    vr.write_text(json.dumps(_rated({"R01": "B", "R03": "A"})), encoding="utf-8")
    out = score(**kw)
    assert out["rater"] == "LLM-rated (Claude)"
    v = out["validation_agreement"]
    assert v["reported_only"] and v["preference_agreement"] == "1/2"
    assert v["per_pair"]["R03"] == {"user": "A", "llm": "tie"}
    # the adoption numbers do not move with the validation ratings
    for k in ("llm_wins", "template_wins", "ties", "pairwise_ok", "llm_wins_of_20",
              "llm_wins_of_informative"):
        assert out[k] == base[k]


def test_validation_blank_partial_and_wrong_ids(files, tmp_path) -> None:
    make_sheet(**sheet_kwargs(files))
    files["ratings_path"].write_text(json.dumps(_rated({"R01": "B", "R02": "A", "R03": "tie"})),
                                     encoding="utf-8")
    vpath = tmp_path / "validation.json"
    vpath.write_text(json.dumps({"sample_ids": ["R01", "R03"]}), encoding="utf-8")
    vr = tmp_path / "vr.json"
    kw = {"sample_path": files["sample_path"], "ratings_path": files["ratings_path"],
          "key_path": files["key_path"], "validation_path": vpath, "validation_ratings_path": vr}
    vr.write_text(json.dumps({s: {"preference": None, "faithful_A": None, "faithful_B": None}
                              for s in ("R01", "R03")}), encoding="utf-8")
    assert score(**kw)["validation_agreement"]["status"] == "not rated"
    vr.write_text(json.dumps({**_rated({"R01": "B"}),
                              "R03": {"preference": None, "faithful_A": None, "faithful_B": None}}),
                  encoding="utf-8")
    with pytest.raises(RatingError):
        score(**kw)
    vr.write_text(json.dumps(_rated({"R01": "B", "R02": "B"})), encoding="utf-8")
    with pytest.raises(RatingError):
        score(**kw)


def test_frozen_validation_sample() -> None:
    import random

    v = json.loads(Path("data/eval/generation_rating_validation.json").read_text(encoding="utf-8"))
    sample = json.loads(Path("data/eval/generation_rating_sample.json").read_text(encoding="utf-8"))
    informative = sorted(s["sample_id"] for s in sample["samples"] if s["class"] != "not_in_force")
    assert v["method"]["seed"] == 20261008 and len(informative) == 18
    assert v["sample_ids"] == sorted(random.Random(20261008).sample(informative, 6))
    assert v["sample_ids"] == ["R04", "R05", "R06", "R07", "R12", "R17"]

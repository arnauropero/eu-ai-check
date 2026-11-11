"""Template renderer and the template baseline run. Text assertions use dev and
synthetic inputs only; held-out is checked by counts, never by its texts."""

from __future__ import annotations

import pytest
from generation_fixtures import load_input, load_synthetic

from eu_ai_check.generation.checks import FAILURE_LEX, NOT_APPLICABLE_LEX
from eu_ai_check.generation.inputs import Inputs
from eu_ai_check.generation.render import (
    NEED_TO_KNOW,
    NEED_TO_KNOW_HEADING,
    PROHIBITED_NOTICE,
    REFERENCE_ONLY,
    render,
    status_line,
)
from eu_ai_check.generation.template_eval import run


def lines(ref: str) -> dict[str, str]:
    return {s.determination_id: s.status_line for s in render(load_input(ref)).sections}


def test_status_lines_per_state() -> None:
    d01 = lines("D01#0")
    assert d01["risk_tier"] == "Risk tier (Article 6): high-risk."
    assert d01["obligation:aiact:art_26"] == "Article 26: applies to this case."
    assert d01["obligation:aiact:art_16"] == "Article 16: does not apply to this case."
    assert d01["actor_role:deployer"] == "Role (deployer): you are a deployer of this system."
    d39 = lines("D39#1")
    assert d39["obligation:aiact:art_26"] == (
        "Article 26: not yet applicable; it applies from 2026-08-02.")
    assert lines("D10#0")["risk_tier"] == (
        "Risk tier (Article 6): could not be determined from the information provided.")


def test_not_in_force_is_never_worded_as_not_applicable() -> None:
    inp = load_synthetic("D21", "2024-03-13", "S02")
    for s in render(inp).sections:
        assert "no version of this provision was in force on 2024-03-13" in s.status_line
        assert "entered into force on 2024-08-01" in s.status_line
        assert not NOT_APPLICABLE_LEX.search(s.status_line)


def test_unresolved_reads_as_missing_information_not_failure() -> None:
    inp = load_input("D10#0")
    section = render(inp).sections[0]
    assert section.need_to_know and not FAILURE_LEX.search(section.status_line)
    text = render(inp).to_text()
    assert NEED_TO_KNOW in text and f"## {NEED_TO_KNOW_HEADING}" in text
    assert "Is the AI system intended to be used as described in this Annex III item?" in text
    assert "Annex III(4)(b)" in text


def test_identical_questions_are_listed_once_with_what_they_affect() -> None:
    inp = load_input("D41#1")
    text = render(inp).to_text()
    tail = text.split(f"## {NEED_TO_KNOW_HEADING}\n", 1)[1]
    assert tail.count("Is the AI system intended to be used as described in this Annex III item?") == 1
    assert tail.count("depends on the risk tier, which is itself unresolved") == 1
    assert "Needed for: Role (provider); Article 16; Article 26" in tail
    assert "Provisions: Annex III(1)(a); Annex III(1)(b)" in tail


def test_grouped_list_covers_every_missing_input_of_every_record() -> None:
    """Rendering-level counterpart of C7: every question of every unresolved
    determination is in the grouped list, under that determination's title, and
    its section points to it. Decided determinations point to nothing."""
    inputs = Inputs()
    for item in inputs.side("dev", repeats=None, synthetic=False) + inputs.synthetic_items():
        answer = render(item.input)
        text = answer.to_text()
        groups = {q: (p, a) for q, p, a in answer.need_to_know_groups()}
        assert len(groups) == len(answer.need_to_know_groups())   # each question once
        for s in answer.sections:
            assert len(s.missing) == len(s.need_to_know)
            for (cite, question), full in zip(s.missing, s.need_to_know):
                assert full == (f"{cite}: {question}" if cite else question)
                provisions, affects = groups[question]
                assert s.title in affects and (cite is None or cite in provisions)
            block = text.split(f"## {s.status_line}\n", 1)[1].split("\n## ", 1)[0]
            assert (NEED_TO_KNOW in block) == bool(s.missing)


def test_applicable_obligations_show_their_text_and_sources() -> None:
    inp = load_input("D01#0")
    s26 = next(s for s in render(inp).sections if s.determination_id == "obligation:aiact:art_26")
    assert len(s26.obligation_texts) == len(s26.citations) == 12
    assert s26.citations[1] == ("Article 26(2)", "aiact:art_26:para_2@2024-08-01")


def test_prohibited_practice_comes_first_and_marks_high_risk_sections_reference_only() -> None:
    answer = render(load_input("D21#0"))      # prohibited, high-risk, deployer, Art 26 applies
    assert answer.prohibited_applies
    text = answer.to_text()
    assert text.startswith(PROHIBITED_NOTICE + "\n\n## ")
    for s in answer.sections:
        block = text.split(f"## {s.status_line}\n", 1)[1].split("\n## ", 1)[0]
        marked = block.startswith(REFERENCE_ONLY)
        expected = s.determination_id in ("risk_tier", "obligation:aiact:art_26")
        assert marked == expected, s.determination_id
    # Statuses are untouched: the notice is presentation only.
    assert [(s.status, s.status_line) for s in answer.sections] == [
        (s.status, s.status_line) for s in render(load_input("D21#0")).sections]


def test_no_prohibition_no_notice() -> None:
    answer = render(load_input("D01#0"))
    assert not answer.prohibited_applies
    text = answer.to_text()
    assert PROHIBITED_NOTICE not in text and REFERENCE_ONLY not in text


def test_status_line_is_a_function_of_the_input_only() -> None:
    inp = load_input("D01#0")
    for d in inp.determinations:
        assert status_line(d, inp.relevant_date) == status_line(d, inp.relevant_date)


@pytest.fixture(scope="module")
def template_run():
    return run(Inputs())


def test_template_baseline_passes_every_check_on_every_record(template_run) -> None:
    assert (template_run["dev"]["records"], template_run["dev_synthetic"]["records"],
            template_run["heldout"]["records"]) == (84, 3, 39)
    for side in template_run.values():
        assert side["failed"] == 0, side["failed_by_check"]
        assert side["grounded_explanation_coverage"] in (0.0, None)

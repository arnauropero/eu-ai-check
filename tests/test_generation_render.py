"""Template renderer and the template baseline run. Text assertions use dev and
synthetic inputs only; held-out is checked by counts, never by its texts."""

from __future__ import annotations

import pytest
from generation_fixtures import load_input, load_synthetic

from eu_ai_check.generation.checks import FAILURE_LEX, NOT_APPLICABLE_LEX
from eu_ai_check.generation.inputs import Inputs
from eu_ai_check.generation.render import NEED_TO_KNOW, render, status_line
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
    assert NEED_TO_KNOW in text and "Annex III(4)(b): Is the AI system" in text


def test_applicable_obligations_show_their_text_and_sources() -> None:
    inp = load_input("D01#0")
    s26 = next(s for s in render(inp).sections if s.determination_id == "obligation:aiact:art_26")
    assert len(s26.obligation_texts) == len(s26.citations) == 12
    assert s26.citations[1] == ("Article 26(2)", "aiact:art_26:para_2@2024-08-01")


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

"""Checks C0-C8, written test-first: every check must reject hand-built bad output.

Inputs are real cached records through the adapter: D01#0 (high-risk deployer,
Article 26 applies), D10#0 (risk tier unresolved), D38#0 (everything
not_in_force), D39#1 (Article 26 not yet applicable). Each NEGATIVE test names the
check it covers. `NEGATIVE_COVERAGE` lists them, and a test makes sure every check
has at least one.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest
from generation_fixtures import corpus_texts, load_input

from eu_ai_check.generation import checks
from eu_ai_check.generation.adapter import GenerationInput
from eu_ai_check.generation.checks import CHECKS, coverage_counts, provision_refs, run_checks
from eu_ai_check.generation.render import render
from eu_ai_check.generation.schema import Claim, GeneratedAnswer, parse_answer


def ev(inp: GenerationInput, did: str, citation: str) -> str:
    d = inp.determination(did)
    return next(e.evidence_id for e in d.supporting_evidence if e.provision_citation == citation)


def claim(cid: str, did: str, text: str, evidence: list[str], quote: str | None = None,
          quote_ev: str | None = None) -> Claim:
    return Claim(claim_id=cid, determination_id=did, text=text, evidence_ids=evidence,
                 quote=quote, quote_evidence_id=quote_ev)


def check(inp: GenerationInput, answer: GeneratedAnswer | None, rendered=None,
          parse_error: str | None = None):
    rendered = rendered if rendered is not None else render(inp, answer)
    return run_checks(inp, rendered, answer, parse_error=parse_error, corpus_texts=corpus_texts())


@pytest.fixture(scope="module")
def d01() -> GenerationInput:
    return load_input("D01#0")


@pytest.fixture(scope="module")
def d10() -> GenerationInput:
    return load_input("D10#0")


@pytest.fixture(scope="module")
def d38() -> GenerationInput:
    return load_input("D38#0")


@pytest.fixture(scope="module")
def d39() -> GenerationInput:
    return load_input("D39#1")


def good_d01(inp: GenerationInput) -> GeneratedAnswer:
    annex = ev(inp, "risk_tier", "Annex III(4)(a)")
    p26_2 = ev(inp, "obligation:aiact:art_26", "Article 26(2)")
    return GeneratedAnswer(claims=[
        claim("c1", "risk_tier", "Annex III(4)(a) lists AI systems used to recruit or "
              "select natural persons.", [annex]),
        claim("c2", "obligation:aiact:art_26", "Article 26(2) is about human oversight by "
              "competent people.", [p26_2],
              "assign human oversight to natural persons", p26_2),
    ])


# ---------------------------------------------------------------------------
# Positive controls
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ref", ["D01#0", "D10#0", "D38#0", "D39#1", "D32#2", "D31#0"])
def test_template_passes_every_check(ref) -> None:
    inp = load_input(ref)
    assert check(inp, None).passed


def test_a_well_formed_answer_passes(d01) -> None:
    report = check(d01, good_d01(d01))
    assert report.passed, report.failures
    assert coverage_counts(d01, good_d01(d01), report)[0] == 2


def test_status_lines_for_the_d32_2_limitation() -> None:
    """The faithful-reproduction case renders 'not high-risk', as the input says."""
    section = render(load_input("D32#2")).sections[0]
    assert section.status_line == "Risk tier (Article 6): not high-risk."


# ---------------------------------------------------------------------------
# NEGATIVE tests, one or more per check
# ---------------------------------------------------------------------------

def test_neg_c0_unparseable_output() -> None:
    answer, error = parse_answer("{not json")
    assert answer is None and error


def test_neg_c0_schema_violation_status_field_smuggled_in(d01) -> None:
    """A model trying to set a status has no field for it: the parse fails."""
    bad = json.dumps({"claims": [{"claim_id": "c1", "determination_id": "risk_tier",
                                  "text": "x", "evidence_ids": [], "quote": None,
                                  "quote_evidence_id": None,
                                  "applicability_status": "not_applicable"}]})
    answer, error = parse_answer(bad)
    assert answer is None
    report = check(d01, None, render(d01, None), parse_error=error)
    report_llm = run_checks(d01, replace(render(d01, None), arm="llm"), None,
                            parse_error=error, corpus_texts=corpus_texts())
    assert "C0" in report.failed_checks() and "C0" in report_llm.failed_checks()


def test_neg_c1_cited_evidence_not_in_input(d01) -> None:
    a = GeneratedAnswer(claims=[claim("c1", "risk_tier", "Annex III lists it.", ["E999"])])
    assert "C1" in check(d01, a).failed_checks()


def test_neg_c1_claim_without_any_citation(d01) -> None:
    a = GeneratedAnswer(claims=[claim("c1", "risk_tier", "It is a recruitment tool.", [])])
    assert "C1" in check(d01, a).failed_checks()


def test_neg_c2_evidence_borrowed_from_another_determination(d01) -> None:
    annex = ev(d01, "risk_tier", "Annex III(4)(a)")
    a = GeneratedAnswer(claims=[claim("c1", "obligation:aiact:art_26",
                                      "Annex III(4)(a) covers recruitment.", [annex])])
    assert "C2" in check(d01, a).failed_checks()


def test_neg_c2_unknown_determination(d01) -> None:
    a = GeneratedAnswer(claims=[claim("c1", "obligation:aiact:art_99", "x", ["E1"])])
    failed = check(d01, a).failed_checks()
    assert "C2" in failed and "C6" in failed


def test_neg_c3_quote_not_in_provision_text(d01) -> None:
    p26_2 = ev(d01, "obligation:aiact:art_26", "Article 26(2)")
    a = GeneratedAnswer(claims=[claim("c1", "obligation:aiact:art_26", "Oversight.", [p26_2],
                                      "assign human oversight to any available staff", p26_2)])
    assert "C3" in check(d01, a).failed_checks()


def test_neg_c3_quote_from_a_citation_only_item(d01) -> None:
    point = ev(d01, "prohibited_practice", "Article 5(1)(a)")
    a = GeneratedAnswer(claims=[claim("c1", "prohibited_practice", "Article 5(1)(a) is about "
                                      "manipulation.", [point], "subliminal techniques", point)])
    assert "C3" in check(d01, a).failed_checks()


def test_neg_c3_quote_source_not_among_the_claims_evidence(d01) -> None:
    p26_1 = ev(d01, "obligation:aiact:art_26", "Article 26(1)")
    p26_2 = ev(d01, "obligation:aiact:art_26", "Article 26(2)")
    a = GeneratedAnswer(claims=[claim("c1", "obligation:aiact:art_26", "Oversight.", [p26_1],
                                      "assign human oversight", p26_2)])
    assert "C3" in check(d01, a).failed_checks()


def test_neg_c3_input_excerpt_differs_from_the_corpus(d01) -> None:
    d = d01.determination("obligation:aiact:art_26")
    tampered_ev = (replace(d.supporting_evidence[0], excerpt="Deployers may do as they like."),
                   *d.supporting_evidence[1:])
    tampered = replace(d01, determinations=tuple(
        replace(x, supporting_evidence=tampered_ev) if x is d else x for x in d01.determinations))
    assert "C3" in check(tampered, None).failed_checks()


def test_neg_c4_rendered_status_contradicts_the_input(d01) -> None:
    rendered = render(d01, None)
    sections = list(rendered.sections)
    i = next(i for i, s in enumerate(sections) if s.determination_id == "obligation:aiact:art_26")
    sections[i] = replace(sections[i], status="not_applicable", reason="conditions_not_met",
                          status_line="Article 26: does not apply to this case.")
    report = check(d01, None, replace(rendered, sections=tuple(sections)))
    assert "C4" in report.failed_checks()


def test_neg_c4_status_line_rewritten_with_same_status(d01) -> None:
    rendered = render(d01, None)
    sections = (replace(rendered.sections[0], status_line="Risk tier: probably high-risk."),
                *rendered.sections[1:])
    assert "C4" in check(d01, None, replace(rendered, sections=sections)).failed_checks()


def test_neg_c4_claim_text_contradicts_an_applicable_status(d01) -> None:
    p26_2 = ev(d01, "obligation:aiact:art_26", "Article 26(2)")
    a = GeneratedAnswer(claims=[claim("c1", "obligation:aiact:art_26",
                                      "Article 26(2) does not apply to a deployer like this.",
                                      [p26_2])])
    assert "C4" in check(d01, a).failed_checks()


def test_neg_c4_hedging_an_applicable_status(d01) -> None:
    annex = ev(d01, "risk_tier", "Annex III(4)(a)")
    a = GeneratedAnswer(claims=[claim("c1", "risk_tier", "Annex III(4)(a) may apply here.",
                                      [annex])])
    assert "C4" in check(d01, a).failed_checks()


def test_neg_c4_unresolved_worded_as_a_system_failure(d10) -> None:
    e = d10.determination("risk_tier").supporting_evidence[0].evidence_id
    a = GeneratedAnswer(claims=[claim("c1", "risk_tier", "The analysis failed for this case.", [e])])
    assert "C4" in check(d10, a).failed_checks()


def test_neg_c4_unresolved_leaning_to_an_outcome(d10) -> None:
    e = ev(d10, "risk_tier", "Annex III(4)(b)")
    a = GeneratedAnswer(claims=[claim("c1", "risk_tier", "Annex III(4)(b) is not applicable "
                                      "to loan audit tools.", [e])])
    assert "C4" in check(d10, a).failed_checks()


def test_neg_c4_decided_status_described_as_not_in_force(d01) -> None:
    point = ev(d01, "prohibited_practice", "Article 5(1)(c)")
    a = GeneratedAnswer(claims=[claim("c1", "prohibited_practice",
                                      "Article 5(1)(c) was not in force.", [point])])
    assert "C4" in check(d01, a).failed_checks()


def test_neg_c4_not_yet_applicable_worded_as_exclusion(d39) -> None:
    d = d39.determination("obligation:aiact:art_26")
    assert d.status_key == "not_applicable/not_yet_applicable"
    e = d.supporting_evidence[0].evidence_id
    a = GeneratedAnswer(claims=[claim("c1", "obligation:aiact:art_26",
                                      "This deployer is exempt from Article 26.", [e])])
    assert "C4" in check(d39, a).failed_checks()


def test_neg_c5_obligation_rendered_without_citation(d01) -> None:
    rendered = render(d01, None)
    sections = tuple(replace(s, citations=()) if s.determination_id == "obligation:aiact:art_26"
                     else s for s in rendered.sections)
    assert "C5" in check(d01, None, replace(rendered, sections=sections)).failed_checks()


def test_neg_c5_applicable_obligation_rendered_without_its_texts(d01) -> None:
    rendered = render(d01, None)
    sections = tuple(replace(s, obligation_texts=s.obligation_texts[:1])
                     if s.determination_id == "obligation:aiact:art_26" else s
                     for s in rendered.sections)
    assert "C5" in check(d01, None, replace(rendered, sections=sections)).failed_checks()


def test_neg_c5_names_a_provision_it_does_not_cite(d01) -> None:
    p26_2 = ev(d01, "obligation:aiact:art_26", "Article 26(2)")
    a = GeneratedAnswer(claims=[claim("c1", "obligation:aiact:art_26",
                                      "Article 50 adds transparency duties.", [p26_2])])
    assert "C5" in check(d01, a).failed_checks()


def test_neg_c5_obligation_language_without_an_obligation_cited(d01) -> None:
    annex = ev(d01, "risk_tier", "Annex III(4)(a)")
    a = GeneratedAnswer(claims=[claim("c1", "risk_tier",
                                      "Recruiters have to keep logs of every ranking.", [annex])])
    assert "C5" in check(d01, a).failed_checks()


def test_neg_c6_missing_determination(d01) -> None:
    rendered = render(d01, None)
    dropped = replace(rendered, sections=rendered.sections[:-1])
    assert "C6" in check(d01, None, dropped).failed_checks()


def test_neg_c6_determination_rendered_twice(d01) -> None:
    rendered = render(d01, None)
    doubled = replace(rendered, sections=(*rendered.sections, rendered.sections[0]))
    assert "C6" in check(d01, None, doubled).failed_checks()


def test_neg_c6_duplicate_claim_ids(d01) -> None:
    good = good_d01(d01)
    a = GeneratedAnswer(claims=[good.claims[0], good.claims[1].model_copy(update={"claim_id": "c1"})])
    assert "C6" in check(d01, a).failed_checks()


def test_neg_c7_unresolved_without_missing_information(d10) -> None:
    rendered = render(d10, None)
    sections = (replace(rendered.sections[0], need_to_know=()), *rendered.sections[1:])
    assert "C7" in check(d10, None, replace(rendered, sections=sections)).failed_checks()


def test_neg_c7_missing_information_on_a_decided_status(d01) -> None:
    rendered = render(d01, None)
    sections = (replace(rendered.sections[0], need_to_know=("Is it a recruitment tool?",)),
                *rendered.sections[1:])
    assert "C7" in check(d01, None, replace(rendered, sections=sections)).failed_checks()


def test_neg_c8_not_in_force_claim_worded_as_not_applicable(d38) -> None:
    a = GeneratedAnswer(claims=[claim("c1", "obligation:aiact:art_16",
                                      "Article 16 does not apply to this provider.", ["E1"])])
    failed = check(d38, a).failed_checks()
    assert "C8" in failed


def test_neg_c8_not_in_force_status_line_rewritten_as_not_applicable(d38) -> None:
    rendered = render(d38, None)
    sections = (replace(rendered.sections[0],
                        status_line="Risk tier (Article 6): not applicable to this case."),
                *rendered.sections[1:])
    failed = check(d38, None, replace(rendered, sections=sections)).failed_checks()
    assert "C8" in failed and "C4" in failed


#: Which negative tests cover which check (reported in the Phase 4 summary).
NEGATIVE_COVERAGE = {
    "C0": ["test_neg_c0_unparseable_output",
           "test_neg_c0_schema_violation_status_field_smuggled_in"],
    "C1": ["test_neg_c1_cited_evidence_not_in_input", "test_neg_c1_claim_without_any_citation"],
    "C2": ["test_neg_c2_evidence_borrowed_from_another_determination",
           "test_neg_c2_unknown_determination"],
    "C3": ["test_neg_c3_quote_not_in_provision_text", "test_neg_c3_quote_from_a_citation_only_item",
           "test_neg_c3_quote_source_not_among_the_claims_evidence",
           "test_neg_c3_input_excerpt_differs_from_the_corpus"],
    "C4": ["test_neg_c4_rendered_status_contradicts_the_input",
           "test_neg_c4_status_line_rewritten_with_same_status",
           "test_neg_c4_claim_text_contradicts_an_applicable_status",
           "test_neg_c4_hedging_an_applicable_status",
           "test_neg_c4_unresolved_worded_as_a_system_failure",
           "test_neg_c4_unresolved_leaning_to_an_outcome",
           "test_neg_c4_decided_status_described_as_not_in_force",
           "test_neg_c4_not_yet_applicable_worded_as_exclusion"],
    "C5": ["test_neg_c5_obligation_rendered_without_citation",
           "test_neg_c5_applicable_obligation_rendered_without_its_texts",
           "test_neg_c5_names_a_provision_it_does_not_cite",
           "test_neg_c5_obligation_language_without_an_obligation_cited"],
    "C6": ["test_neg_c6_missing_determination", "test_neg_c6_determination_rendered_twice",
           "test_neg_c6_duplicate_claim_ids", "test_neg_c2_unknown_determination"],
    "C7": ["test_neg_c7_unresolved_without_missing_information",
           "test_neg_c7_missing_information_on_a_decided_status"],
    "C8": ["test_neg_c8_not_in_force_claim_worded_as_not_applicable",
           "test_neg_c8_not_in_force_status_line_rewritten_as_not_applicable"],
}


def test_every_check_has_a_negative_test() -> None:
    assert set(NEGATIVE_COVERAGE) == set(CHECKS)
    for names in NEGATIVE_COVERAGE.values():
        for name in names:
            assert name in globals(), name


def test_provision_reference_parsing() -> None:
    assert provision_refs("see Article 26(2) and Art. 16(a)") == [
        ("article", "26", ("2",)), ("article", "16", ("a",))]
    assert provision_refs("Annex III, point 4(a)") == [("annex", "III", ("4", "a"))]
    assert provision_refs("Annex III(4)(a)") == [("annex", "III", ("4", "a"))]


#: SHA-256 of the C4/C5/C8 lexicons, fixed before the first generator call (§18).
LEXICON_SHA = "11ac0cf8dfe4e9eb928c5b49c2c9b6caa4e55f1979907c2c91238ba73f749a9a"


def test_lexicons_are_pinned() -> None:
    lexicons = {name: getattr(checks, name).pattern for name in sorted(dir(checks))
                if name.endswith(("_LEX", "_LANG"))}
    by_status = {status: [lex.pattern for lex in lexs]
                 for status, lexs in checks.FORBIDDEN_BY_STATUS.items()}
    payload = json.dumps({"lexicons": lexicons, "forbidden_by_status": by_status},
                         sort_keys=True)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    assert digest == LEXICON_SHA, digest


def test_c4_diagnostics_name_phrase_and_claim_without_changing_the_verdict(d01) -> None:
    p26_2 = ev(d01, "obligation:aiact:art_26", "Article 26(2)")
    a = GeneratedAnswer(claims=[claim("c1", "obligation:aiact:art_26",
                                      "Article 26(2) does not apply to a deployer like this.",
                                      [p26_2])])
    report = check(d01, a)
    (d,) = report.diagnostics
    assert d["phrase"] == "does not apply" and d["lexicon"] == "NOT_APPLICABLE_LEX"
    assert d["claim_id"] == "c1" and d["claim_text"].startswith("Article 26(2) does not apply")
    assert d["status"] == "applicable" and not report.passed
    assert check(d01, good_d01(d01)).diagnostics == []
    assert "?" not in checks.LEXICON_NAMES.values() and len(checks.LEXICON_NAMES) == 8

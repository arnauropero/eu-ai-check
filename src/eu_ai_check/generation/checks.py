"""Deterministic checks C0-C8 on a rendered answer (SYSTEM_SPEC.md §18). No LLM.

Each check returns a list of failure messages, and an empty list is a pass. C4(b),
C5(b-c) and C8 are lexical. They catch status vocabulary, uncited provision
references and uncited obligation language, but a paraphrase can slip past them,
so a pass is not a proof of faithfulness (§18). The lexicons are fixed here,
before the first generator call, and are pinned by tests.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..determination.matcher import _norm
from .adapter import APPLICABLE, NOT_IN_FORCE, UNRESOLVED, Determination, GenerationInput
from .render import RenderedAnswer, status_line
from .schema import Claim, GeneratedAnswer

CHECKS = ("C0", "C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8")


def _lex(*patterns: str) -> re.Pattern:
    return re.compile("|".join(f"(?:{p})" for p in patterns), re.IGNORECASE)


#: Words that state or imply "does not apply".
NOT_APPLICABLE_LEX = _lex(
    r"\bdo(?:es)? not apply\b", r"\bdo(?:es)?n['’]t apply\b", r"\bnot applicable\b",
    r"\binapplicable\b", r"\bexempt(?:ed|ion)?\b", r"\bexcluded\b", r"\bout of scope\b",
    r"\boutside (?:the |its )?scope\b", r"\bnot covered\b", r"\bnot subject to\b",
    r"\bnot (?:a )?high[- ]risk\b", r"\bnot prohibited\b", r"\bnot (?:a |the )?(?:provider|deployer)\b",
)
#: Words that state or imply "applies".
APPLIES_LEX = _lex(
    r"\bapplies to (?:you|your|this|the case|the system)\b", r"\byou must\b",
    r"\byou are required\b", r"\byou have to\b", r"\bis (?:a )?high[- ]risk\b",
    r"\b(?:is|are) prohibited\b", r"\bfalls under (?:a|the|this) prohibition\b",
    r"\byou are (?:a|the) (?:provider|deployer)\b", r"\bqualif(?:y|ies) as\b",
)
#: Hedging that would turn a definite status into a possibility.
HEDGE_LEX = _lex(
    r"\b(?:may|might|could) (?:apply|be (?:high[- ]risk|prohibited|required))\b",
    r"\bpossibly\b", r"\bunclear whether\b",
)
#: Words that state the determination is undecided.
UNDECIDED_LEX = _lex(r"\bcould not be determined\b", r"\bcannot be determined\b",
                     r"\bunresolved\b", r"\bundetermined\b")
#: Words that state the provision was not in force.
NOT_IN_FORCE_LEX = _lex(r"\bnot (?:yet )?in force\b", r"\bno version\b")
#: Wording of a system failure, forbidden for unresolved (§3 of the design note).
FAILURE_LEX = _lex(r"\berror\b", r"\bfail(?:ed|ure)?\b", r"\bunable to\b",
                   r"\bcould not (?:process|assess|analy[sz]e)\b")
#: The subset of not-applicable wording that reads as a permanent exclusion.
EXCLUSION_LEX = _lex(r"\bexempt(?:ed|ion)?\b", r"\bexcluded\b", r"\bout of scope\b",
                     r"\boutside (?:the |its )?scope\b", r"\bnot covered\b",
                     r"\bnot subject to\b")
#: Obligation language (C5(c)).
OBLIGATION_LANG = _lex(r"\bmust\b", r"\bshall\b", r"\b(?:is|are) required to\b",
                       r"\b(?:has|have) to\b", r"\b(?:is|are) obliged to\b",
                       r"\b(?:is|are) obligated to\b", r"\bobligation to\b")

#: C4(b): lexicons a claim may not use, by the status of its own determination.
#: `not_in_force` + NOT_APPLICABLE_LEX is C8, counted separately.
FORBIDDEN_BY_STATUS: dict[str, tuple[re.Pattern, ...]] = {
    "applicable": (NOT_APPLICABLE_LEX, HEDGE_LEX, UNDECIDED_LEX, NOT_IN_FORCE_LEX),
    "not_applicable/conditions_not_met": (APPLIES_LEX, HEDGE_LEX, UNDECIDED_LEX,
                                          NOT_IN_FORCE_LEX),
    "not_applicable/not_yet_applicable": (EXCLUSION_LEX, UNDECIDED_LEX, NOT_IN_FORCE_LEX),
    "unresolved": (NOT_APPLICABLE_LEX, APPLIES_LEX, HEDGE_LEX, FAILURE_LEX),
    "not_in_force": (APPLIES_LEX, HEDGE_LEX, UNDECIDED_LEX),
}

#: Lexicon names, for diagnostics only (the lexicons themselves are frozen).
LEXICON_NAMES = {id(v): k for k, v in dict(globals()).items()
                 if k.endswith(("_LEX", "_LANG")) and isinstance(v, re.Pattern)}

# Provision references in free text (C5(b)).
_ARTICLE_REF = re.compile(r"\b(?:Article|Art\.?)\s*(\d+)((?:\s*\([0-9a-z]+\))*)", re.IGNORECASE)
_ANNEX_REF = re.compile(
    r"\bAnnex\s+([IVX]+)\b(?:,?\s*(?:Section\s+[A-Z],?\s*)?(?:point\s+)?(\d+)?)?"
    r"((?:\s*\([0-9a-z]+\))*)", re.IGNORECASE)
_PARTS = re.compile(r"\(([0-9a-z]+)\)", re.IGNORECASE)


def provision_refs(text: str) -> list[tuple[str, str, tuple[str, ...]]]:
    """('article', '26', ('2',)) for 'Article 26(2)'; ('annex', 'III', ('4', 'a'))
    for 'Annex III(4)(a)' or 'Annex III, point 4(a)'."""
    refs = []
    for m in _ARTICLE_REF.finditer(text):
        refs.append(("article", m.group(1), tuple(p.lower() for p in _PARTS.findall(m.group(2)))))
    for m in _ANNEX_REF.finditer(text):
        parts = ((m.group(2),) if m.group(2) else ()) + tuple(
            p.lower() for p in _PARTS.findall(m.group(3) or ""))
        refs.append(("annex", m.group(1).upper(), parts))
    return refs


def _resolves(ref: tuple[str, str, tuple[str, ...]], cited: list[str]) -> bool:
    kind, number, parts = ref
    for citation in cited:
        for c_kind, c_number, c_parts in provision_refs(citation):
            if (c_kind, c_number) == (kind, number) and c_parts[:len(parts)] == parts:
                return True
    return False


@dataclass
class CheckReport:
    failures: dict[str, list[str]] = field(default_factory=lambda: {c: [] for c in CHECKS})
    #: claim_id -> checks that claim fails (for coverage: a claim "passes" when empty)
    claim_failures: dict[str, set[str]] = field(default_factory=dict)
    #: C4/C8 diagnostics, reported only: the exact phrase and the claim it came from,
    #: so false positives can be told from real contradictions after the run.
    diagnostics: list[dict] = field(default_factory=list)

    def fail(self, check: str, message: str, claim_id: str | None = None) -> None:
        self.failures[check].append(message)
        if claim_id is not None:
            self.claim_failures.setdefault(claim_id, set()).add(check)

    @property
    def passed(self) -> bool:
        return not any(self.failures.values())

    def failed_checks(self) -> list[str]:
        return [c for c in CHECKS if self.failures[c]]


def _claim_checks(claim: Claim, inp: GenerationInput, report: CheckReport) -> None:
    evidence = inp.evidence()
    det = inp.determination(claim.determination_id)
    cid = claim.claim_id

    # C1: every cited id exists; a claim with no citation is not traceable.
    if not claim.evidence_ids:
        report.fail("C1", f"{cid}: cites no evidence", cid)
    for e in claim.evidence_ids:
        if e not in evidence:
            report.fail("C1", f"{cid}: evidence {e!r} not in the input", cid)

    # C2: evidence belongs to the claim's own determination.
    if det is None:
        report.fail("C2", f"{cid}: unknown determination {claim.determination_id!r}", cid)
        return
    own = {e.evidence_id for e in det.supporting_evidence}
    for e in claim.evidence_ids:
        if e in evidence and e not in own:
            report.fail("C2", f"{cid}: evidence {e} belongs to another determination", cid)

    # C3: a quote is verbatim from one of the claim's own cited excerpts.
    if (claim.quote is None) != (claim.quote_evidence_id is None):
        report.fail("C3", f"{cid}: quote and quote_evidence_id must be set together", cid)
    elif claim.quote is not None:
        source = evidence.get(claim.quote_evidence_id)
        if claim.quote_evidence_id not in claim.evidence_ids:
            report.fail("C3", f"{cid}: quote source is not among the claim's evidence", cid)
        elif source is None or source.excerpt is None:
            report.fail("C3", f"{cid}: quote source has no excerpt", cid)
        elif not _norm(claim.quote) or _norm(claim.quote) not in _norm(source.excerpt):
            report.fail("C3", f"{cid}: quote is not verbatim in {source.provision_citation}", cid)

    # C4(b) and C8: status vocabulary in the claim text.
    def diagnose(check: str, lexicon: re.Pattern, m: re.Match) -> None:
        report.diagnostics.append({
            "check": check, "kind": "claim_lexicon", "claim_id": cid,
            "determination_id": det.determination_id, "status": det.status_key,
            "lexicon": LEXICON_NAMES.get(id(lexicon), "?"), "phrase": m.group(0),
            "span": [m.start(), m.end()], "claim_text": claim.text})

    for lexicon in FORBIDDEN_BY_STATUS[det.status_key]:
        m = lexicon.search(claim.text)
        if m:
            report.fail("C4", f"{cid}: {m.group(0)!r} contradicts status {det.status_key}", cid)
            diagnose("C4", lexicon, m)
    if det.applicability_status == NOT_IN_FORCE:
        m = NOT_APPLICABLE_LEX.search(claim.text)
        if m:
            report.fail("C8", f"{cid}: not_in_force worded as not_applicable ({m.group(0)!r})", cid)
            diagnose("C8", NOT_APPLICABLE_LEX, m)

    # C5(b): every provision named in the text is one this claim cites.
    cited = [evidence[e].provision_citation for e in claim.evidence_ids if e in evidence]
    for ref in provision_refs(claim.text):
        if not _resolves(ref, cited):
            report.fail("C5", f"{cid}: names {ref[0]} {ref[1]}{''.join(f'({p})' for p in ref[2])} "
                              "without citing it", cid)
    # C5(c): obligation language only with an obligation provision cited.
    m = OBLIGATION_LANG.search(claim.text)
    if m and not any(evidence[e].provision_type == "obligation"
                     for e in claim.evidence_ids if e in evidence):
        report.fail("C5", f"{cid}: obligation language {m.group(0)!r} without an obligation "
                          "provision cited", cid)


def run_checks(inp: GenerationInput, rendered: RenderedAnswer,
               answer: GeneratedAnswer | None, *, parse_error: str | None = None,
               corpus_texts: dict[str, str]) -> CheckReport:
    """`answer` is None for the template arm (nothing to parse) and for an LLM
    response that failed to parse, in which case `parse_error` is set and C0 fails.
    `corpus_texts` maps versioned_provision_id -> canonical text (for C3)."""
    report = CheckReport()
    by_id = {d.determination_id: d for d in inp.determinations}

    # C0: the model output parsed against the strict schema.
    if parse_error is not None:
        report.fail("C0", parse_error)
    if rendered.arm == "llm" and answer is None and parse_error is None:
        report.fail("C0", "llm arm rendered without a parsed answer")

    # C3 (second half): every excerpt is the corpus text of its version.
    for d in inp.determinations:
        for e in d.supporting_evidence:
            if e.excerpt is not None and corpus_texts.get(e.versioned_provision_id) != e.excerpt:
                report.fail("C3", f"{e.evidence_id}: excerpt differs from corpus text of "
                                  f"{e.versioned_provision_id}")
    for s in rendered.sections:
        texts = {e.provision_citation: e.excerpt
                 for e in (by_id[s.determination_id].supporting_evidence
                           if s.determination_id in by_id else ())}
        for cite, text in s.obligation_texts:
            if texts.get(cite) != text:
                report.fail("C3", f"{s.determination_id}: rendered text of {cite} is not its excerpt")

    # C6: each input determination rendered exactly once; nothing extra.
    rendered_ids = [s.determination_id for s in rendered.sections]
    for did in by_id:
        if rendered_ids.count(did) != 1:
            report.fail("C6", f"{did} rendered {rendered_ids.count(did)} times")
    for did in rendered_ids:
        if did not in by_id:
            report.fail("C6", f"{did} rendered but not in the input")
    for cid in rendered.extra:
        report.fail("C6", f"claim {cid} refers to a determination not in the input", cid)
    if answer is not None:
        ids = [c.claim_id for c in answer.claims]
        for cid in {i for i in ids if ids.count(i) > 1}:
            report.fail("C6", f"claim id {cid} is not unique", cid)

    for s in rendered.sections:
        d = by_id.get(s.determination_id)
        if d is None:
            continue
        # C4(a): the rendered status is the input status, in the fixed wording.
        if (s.status, s.reason) != (d.applicability_status, d.not_applicable_reason):
            report.fail("C4", f"{d.determination_id}: rendered status {s.status}/{s.reason} "
                              f"!= input {d.status_key}")
            report.diagnostics.append({"check": "C4", "kind": "rendered_status",
                                       "determination_id": d.determination_id,
                                       "status": d.status_key,
                                       "rendered": f"{s.status}/{s.reason}"})
        if s.status_line != status_line(d, inp.relevant_date):
            report.fail("C4", f"{d.determination_id}: status line is not the code wording")
            report.diagnostics.append({"check": "C4", "kind": "status_line",
                                       "determination_id": d.determination_id,
                                       "status": d.status_key, "rendered": s.status_line})
        # C5(a): every obligation article carries its own citations.
        if d.determination_type == "obligation_applicability" and d.applicability_status != NOT_IN_FORCE:
            own = tuple((e.provision_citation, e.versioned_provision_id)
                        for e in d.supporting_evidence)
            if not own or s.citations != own:
                report.fail("C5", f"{d.determination_id}: obligation rendered without its citations")
            if d.applicability_status == APPLICABLE and len(s.obligation_texts) != len(own):
                report.fail("C5", f"{d.determination_id}: applicable obligation without its texts")
        # C7: unresolved renders exactly its missing inputs; nothing else does.
        expected = tuple(m.question for m in d.unresolved_inputs)
        if d.applicability_status == UNRESOLVED and (not expected or s.need_to_know != expected):
            report.fail("C7", f"{d.determination_id}: missing-information list incomplete")
        if d.applicability_status != UNRESOLVED and s.need_to_know:
            report.fail("C7", f"{d.determination_id}: missing-information list on a decided status")
        # C8 also covers the code-written line.
        if d.applicability_status == NOT_IN_FORCE and NOT_APPLICABLE_LEX.search(s.status_line):
            report.fail("C8", f"{d.determination_id}: not_in_force status line reads as not_applicable")

    if answer is not None:
        for claim in answer.claims:
            _claim_checks(claim, inp, report)
    return report


def is_explainable(d: Determination) -> bool:
    """The determinations grounded-explanation coverage counts (§18)."""
    if d.applicability_status == UNRESOLVED:
        return True
    if d.determination_id in ("risk_tier", "prohibited_practice"):
        return d.status_key in ("applicable", "not_applicable/conditions_not_met")
    if d.determination_type == "obligation_applicability":
        return d.status_key in ("applicable", "not_applicable/not_yet_applicable")
    return False


def coverage_counts(inp: GenerationInput, answer: GeneratedAnswer | None,
                    report: CheckReport | None) -> tuple[int, int]:
    """(explainable determinations with >= 1 passing claim, explainable total) for
    one shipped answer. A fallback (answer None) covers nothing."""
    explainable = [d for d in inp.determinations if is_explainable(d)]
    if answer is None or report is None or not report.passed:
        return 0, len(explainable)
    covered = 0
    for d in explainable:
        if any(c.determination_id == d.determination_id
               and not report.claim_failures.get(c.claim_id) for c in answer.claims):
            covered += 1
    return covered, len(explainable)

"""Rendering an answer from a `GenerationInput` (SYSTEM_SPEC.md §16.3). No LLM.

One renderer serves both arms. The template baseline is `render(inp)`, with no
claims. The LLM arm is `render(inp, answer)`, which uses the same code-written
skeleton plus the model's claims. Status lines, citations, missing-information
lists and obligation texts are always written here, from the input. The model can
add claims, and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .adapter import (
    ACTOR_ROLE,
    APPLICABLE,
    NOT_IN_FORCE,
    OBLIGATION,
    PROHIBITED,
    RISK_TIER,
    UNRESOLVED,
    Determination,
    EvidenceItem,
    GenerationInput,
)
from .schema import Claim, GeneratedAnswer

#: Fixed rendering order (§16.3).
ORDER = (RISK_TIER, PROHIBITED, ACTOR_ROLE, OBLIGATION)
NEED_TO_KNOW = "To decide this, we would need to know:"


def title(d: Determination) -> str:
    if d.determination_id == RISK_TIER:
        return "Risk tier (Article 6)"
    if d.determination_id == PROHIBITED:
        return "Prohibited practices (Article 5)"
    if d.determination_type == ACTOR_ROLE:
        return f"Role ({d.determination_id.split(':', 1)[1]})"
    if d.determination_type == OBLIGATION:
        return f"Article {d.determination_id.rsplit('art_', 1)[1]}"
    raise ValueError(d.determination_id)


def status_line(d: Determination, relevant_date) -> str:
    """The only place a status is put into words. Never worded as a failure, and
    `not_in_force` never as `not_applicable` (§14, §16.3)."""
    t = title(d)
    s, reason = d.applicability_status, d.not_applicable_reason
    if s == NOT_IN_FORCE:
        return (f"{t}: no version of this provision was in force on {relevant_date}. "
                f"The text entered into force on {d.in_force_from}.")
    if s == UNRESOLVED:
        return f"{t}: could not be determined from the information provided."
    if reason == "not_yet_applicable":
        subject = {RISK_TIER: "the high-risk classification",
                   PROHIBITED: "the prohibition"}.get(d.determination_id, "it")
        return f"{t}: not yet applicable; {subject} applies from {d.applicable_from}."
    positive = s == APPLICABLE
    if d.determination_id == RISK_TIER:
        return f"{t}: {'high-risk' if positive else 'not high-risk'}."
    if d.determination_id == PROHIBITED:
        return (f"{t}: the described practice falls under a prohibition." if positive
                else f"{t}: no prohibited practice was found for this case.")
    if d.determination_type == ACTOR_ROLE:
        role = d.determination_id.split(":", 1)[1]
        return (f"{t}: you are a {role} of this system." if positive
                else f"{t}: you are not a {role} of this system.")
    return (f"{t}: applies to this case." if positive
            else f"{t}: does not apply to this case.")


@dataclass(frozen=True)
class RenderedClaim:
    claim_id: str
    text: str
    citations: tuple[str, ...]          # provision_citation of each cited evidence id
    quote: str | None
    quote_citation: str | None


@dataclass(frozen=True)
class Section:
    determination_id: str
    status: str
    reason: str | None
    status_line: str
    citations: tuple[tuple[str, str], ...]          # (provision_citation, versioned id)
    obligation_texts: tuple[tuple[str, str], ...]   # (provision_citation, excerpt)
    need_to_know: tuple[str, ...]
    claims: tuple[RenderedClaim, ...] = ()


@dataclass(frozen=True)
class RenderedAnswer:
    case_ref: str
    arm: str                            # "template" | "llm"
    sections: tuple[Section, ...]
    extra: tuple[str, ...] = field(default=())   # claims whose determination is unknown

    def to_text(self) -> str:
        lines: list[str] = []
        for s in self.sections:
            lines += [f"## {s.status_line}"]
            for c in s.claims:
                lines.append(f"- {c.text} [{'; '.join(c.citations)}]")
                if c.quote:
                    lines.append(f"  > \"{c.quote}\" ({c.quote_citation})")
            if s.need_to_know:
                lines.append(NEED_TO_KNOW)
                lines += [f"- {q}" for q in s.need_to_know]
            for cite, text in s.obligation_texts:
                lines.append(f"- {cite}: {text}")
            if s.citations:
                lines.append("Sources: " + "; ".join(f"{c} ({v})" for c, v in s.citations))
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"


def _ordered(inp: GenerationInput) -> list[Determination]:
    return sorted(inp.determinations,
                  key=lambda d: (ORDER.index(d.determination_type
                                             if d.determination_type in ORDER else d.determination_id),
                                 d.determination_id))


def _need_to_know(d: Determination) -> tuple[str, ...]:
    return tuple(m.question for m in d.unresolved_inputs)


def _render_claim(c: Claim, evidence: dict[str, EvidenceItem]) -> RenderedClaim:
    cites = tuple(evidence[e].provision_citation if e in evidence else f"<unknown {e}>"
                  for e in c.evidence_ids)
    quote_cite = None
    if c.quote_evidence_id:
        e = evidence.get(c.quote_evidence_id)
        quote_cite = e.provision_citation if e else f"<unknown {c.quote_evidence_id}>"
    return RenderedClaim(c.claim_id, c.text, cites, c.quote, quote_cite)


def render(inp: GenerationInput, answer: GeneratedAnswer | None = None) -> RenderedAnswer:
    evidence = inp.evidence()
    claims = list(answer.claims) if answer else []
    sections = []
    for d in _ordered(inp):
        mine = [c for c in claims if c.determination_id == d.determination_id]
        obligation_texts = ()
        if d.determination_type == OBLIGATION:
            obligation_texts = tuple((e.provision_citation, e.excerpt)
                                     for e in d.supporting_evidence if e.excerpt)
        sections.append(Section(
            determination_id=d.determination_id,
            status=d.applicability_status,
            reason=d.not_applicable_reason,
            status_line=status_line(d, inp.relevant_date),
            citations=tuple((e.provision_citation, e.versioned_provision_id)
                            for e in d.supporting_evidence),
            obligation_texts=obligation_texts,
            need_to_know=_need_to_know(d),
            claims=tuple(_render_claim(c, evidence) for c in mine),
        ))
    known = {d.determination_id for d in inp.determinations}
    extra = tuple(c.claim_id for c in claims if c.determination_id not in known)
    return RenderedAnswer(inp.case_ref, "llm" if answer else "template", tuple(sections), extra)

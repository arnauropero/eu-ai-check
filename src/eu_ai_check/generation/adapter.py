"""Phase 3 records -> the generation input contract (SYSTEM_SPEC.md §16.1-16.2). No LLM.

A cached Phase 3 record is replayed through `Pipeline` from its recorded verdicts
(no API call) and must reproduce the record exactly. The full in-memory
`pipeline.Determination` is then mapped onto the §16 contract.

What the generator may see is decided here (invariant 23): no description, no
free-text facts, no `case_quote`, no rationale except those of `insufficient`
verdicts listed in `unresolved_inputs`, no uncited verdicts, no eval labels.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any

from ..corpus.models import Corpus, Provision
from ..determination.combine import (
    ANNEX_III,
    ARTICLE_6_1,
    ARTICLE_6_1_B,
    OVERRIDE_ID,
    ROUTE_PROVISION,
    prohibited_practice,
    risk_tier,
)
from ..determination.evaluation import Run
from ..determination.logic import (
    APPLICABLE,
    NA_CONDITIONS,
    NA_NOT_YET,
    NOT_IN_FORCE,
    UNRESOLVED,
)
from ..determination.matcher import (
    DOES_NOT_MATCH,
    INSUFFICIENT,
    MATCHES,
    MatchResult,
    Verdict,
)
from ..determination.pipeline import OBLIGATION_GROUPS, Pipeline
from ..determination.pipeline import Determination as PipelineDetermination
from ..determination.scopes import SCOPE_BY_NAME, scope_of
from ..extraction.schema import CaseFacts

RISK_TIER = "risk_tier"
PROHIBITED = "prohibited_practice"
ACTOR_ROLE = "actor_role"
OBLIGATION = "obligation_applicability"

RISK_TIER_SCOPES = ("annex_iii", "article_6_3_condition", "article_6_3_override",
                    "annex_i", "article_6_1_b")
ARTICLE_5_POINTS = "aiact:art_5:para_1:point_"
ROLE_FACTS = {
    "deployer": ("uses_system",),
    "provider": ("develops_system",),
}
TRANSFER_FACTS = ("puts_own_name_or_trademark", "modifies_intended_purpose",
                  "modifies_safety_relevant_components")


class AdapterError(ValueError):
    """The record cannot be mapped without choosing something the data does not say."""


# --------------------------------------------------------------------------
# The contract (§16, §16.2)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class EvidenceItem:
    evidence_id: str
    logical_provision_id: str
    versioned_provision_id: str
    provision_citation: str            # e.g. "Article 26(2)"
    excerpt: str | None                # canonical text; None = citation only (§16.2)
    provision_type: str                # corpus metadata, used by check C5(c)


@dataclass(frozen=True)
class MissingInput:
    kind: str                          # matcher_question | case_fact | upstream_determination
    ref: str
    question: str                      # code-owned
    rationale: str | None = None       # matcher rationale, insufficient verdicts only


@dataclass(frozen=True)
class Determination:
    determination_id: str
    determination_type: str
    applicability_status: str          # applicable | not_applicable | unresolved | not_in_force
    not_applicable_reason: str | None  # conditions_not_met | not_yet_applicable | None
    explanation: str                   # built by code, never by an LLM
    supporting_evidence: tuple[EvidenceItem, ...] = ()
    unresolved_inputs: tuple[MissingInput, ...] = ()
    # Rendering facts, all from the corpus or the rules, never from the model:
    applicable_from: date | None = None    # set iff not_yet_applicable
    in_force_from: date | None = None      # set iff not_in_force
    # Risk tier only (§16):
    system_category: tuple[str, ...] = ()
    derogation: str | None = None

    @property
    def status_key(self) -> str:
        """The Phase 3 compound form, e.g. 'not_applicable/not_yet_applicable'."""
        if self.not_applicable_reason:
            return f"{self.applicability_status}/{self.not_applicable_reason}"
        return self.applicability_status


@dataclass(frozen=True)
class GenerationInput:
    case_ref: str
    relevant_date: date
    determinations: tuple[Determination, ...]

    def determination(self, determination_id: str) -> Determination | None:
        return next((d for d in self.determinations
                     if d.determination_id == determination_id), None)

    def evidence(self) -> dict[str, EvidenceItem]:
        return {e.evidence_id: e for d in self.determinations for e in d.supporting_evidence}

    def as_prompt_data(self) -> dict[str, Any]:
        """Exactly what the model sees. Versioned ids and provision types are
        bookkeeping for code and are left out to keep the prompt small."""
        return {
            "relevant_date": self.relevant_date.isoformat(),
            "determinations": [
                {
                    "determination_id": d.determination_id,
                    "determination_type": d.determination_type,
                    "applicability_status": d.applicability_status,
                    "not_applicable_reason": d.not_applicable_reason,
                    "explanation": d.explanation,
                    "evidence": [
                        {"evidence_id": e.evidence_id, "citation": e.provision_citation,
                         "excerpt": e.excerpt}
                        for e in d.supporting_evidence
                    ],
                    "unresolved_inputs": [
                        {"question": m.question, "rationale": m.rationale}
                        for m in d.unresolved_inputs
                    ],
                }
                for d in self.determinations
            ],
        }


def split_status(status: str) -> tuple[str, str | None]:
    if "/" in status:
        head, reason = status.split("/", 1)
        return head, reason
    return status, None


# --------------------------------------------------------------------------
# Replay (zero cost, exact)
# --------------------------------------------------------------------------

class ReplayMatcher:
    """Returns a record's recorded verdicts. Never calls a model."""

    name = "replay"

    def __init__(self, record: dict[str, Any]):
        self.record = record

    def match(self, description: str, candidates: dict[str, list[Provision]]) -> MatchResult:
        r = self.record
        return MatchResult(
            verdicts={pid: Verdict(pid, v["verdict"], v["case_quote"], v["provision_quote"],
                                   v["rationale"], v["model_verdict"], v["downgrade"])
                      for pid, v in (r.get("verdicts") or {}).items()},
            log=r.get("verification_log", []), error=r.get("match_error"),
            served_model=r.get("served_model"), input_tokens=r.get("input_tokens", 0),
            output_tokens=r.get("output_tokens", 0))


class NoMatcher:
    """For synthetic not_in_force cases: any matcher call is an error."""

    name = "none"

    def match(self, description: str, candidates: dict[str, list[Provision]]) -> MatchResult:
        raise AdapterError("a not_in_force replay reached the matcher")


def _norm_record(rec: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(rec, ensure_ascii=False, default=str))


def replay(pipeline: Pipeline, record: dict[str, Any], case: dict[str, Any],
           on: date) -> PipelineDetermination:
    """Rebuild the in-memory determination of a cached record and require that
    it reproduces the record exactly (the retry script's check)."""
    det = pipeline.determine(CaseFacts.model_validate(case["gold_facts"]),
                             case["description"], on, ReplayMatcher(record))
    rebuilt = Run(record["case"], record["repeat"], det).record(record["arm"])
    if _norm_record(rebuilt) != _norm_record(record):
        raise AdapterError(f"replay does not reproduce {record['case']}#{record['repeat']}")
    return det


def replay_synthetic(pipeline: Pipeline, case: dict[str, Any],
                     on: date) -> PipelineDetermination:
    det = pipeline.determine(CaseFacts.model_validate(case["gold_facts"]),
                             case["description"], on, NoMatcher())
    if det.risk_tier.status != NOT_IN_FORCE:
        raise AdapterError(f"synthetic case at {on} is not not_in_force")
    return det


# --------------------------------------------------------------------------
# Mapping
# --------------------------------------------------------------------------

class _Builder:
    def __init__(self, corpus: Corpus, pipeline: Pipeline, on: date):
        self.on = on
        self.pipeline = pipeline
        self.provisions = {p.logical_provision_id: p for p in corpus.provisions}
        self.counter = 0

    def evidence(self, pid: str, with_excerpt: bool = True) -> EvidenceItem:
        p = self.provisions[pid]
        if not p.text_valid_on(self.on):
            raise AdapterError(f"{pid} has no version valid on {self.on}")
        self.counter += 1
        return EvidenceItem(
            evidence_id=f"E{self.counter}",
            logical_provision_id=pid,
            versioned_provision_id=p.versioned_provision_id,
            provision_citation=short_citation(p),
            excerpt=p.text if with_excerpt else None,
            provision_type=p.provision_type.value,
        )

    def in_force_from(self, pids: list[str]) -> date:
        return min(self.provisions[pid].text_valid_from for pid in pids)


def short_citation(p: Provision) -> str:
    """The last element of the path rendering: 'Article 26(2)', 'Annex III(4)(a)'."""
    return p.citation().split(" -> ")[-1]


def _flip_matters(verdicts: dict[str, Verdict], pid: str, decide) -> bool:
    """Would setting this one insufficient verdict to matches, or to
    does_not_match, change the outcome under the fixed combination code?"""
    base = decide(verdicts)
    for value in (MATCHES, DOES_NOT_MATCH):
        flipped = dict(verdicts)
        flipped[pid] = replace(verdicts[pid], verdict=value)
        if decide(flipped) != base:
            return True
    return False


def _question(scope: str) -> str:
    return SCOPE_BY_NAME[scope].question


def _matcher_missing(verdicts: dict[str, Verdict], scopes: tuple[str, ...], decide,
                     b: _Builder) -> tuple[list[MissingInput], list[EvidenceItem]]:
    """The insufficient verdicts whose flip could change the outcome (§16.2)."""
    candidates = [pid for pid, v in verdicts.items()
                  if v.verdict == INSUFFICIENT and scope_of(pid) in scopes]
    decisive = [pid for pid in candidates if _flip_matters(verdicts, pid, decide)]
    missing, evidence = [], []
    for pid in sorted(decisive):
        p = b.provisions[pid]
        missing.append(MissingInput(
            "matcher_question", pid,
            f"{short_citation(p)}: {_question(scope_of(pid))}",
            verdicts[pid].rationale or None))
        evidence.append(b.evidence(pid))
    return missing, evidence


def _risk_tier(det: PipelineDetermination, b: _Builder) -> Determination:
    tier = det.risk_tier
    status, reason = split_status(tier.status)
    if status == NOT_IN_FORCE:
        route_ids = list(ROUTE_PROVISION.values())
        return Determination(RISK_TIER, RISK_TIER, status, None,
                             "no classification provision was in force on the date",
                             in_force_from=b.in_force_from(route_ids))
    verdicts = det.match.verdicts
    engine = b.pipeline.engine

    def decide(vs):
        return risk_tier(vs, b.on, engine).status

    evidence = [b.evidence(ROUTE_PROVISION[ANNEX_III]), b.evidence(ROUTE_PROVISION[ARTICLE_6_1])]
    for pid in sorted(tier.matched):
        if scope_of(pid) in RISK_TIER_SCOPES:
            evidence.append(b.evidence(pid))
    missing: list[MissingInput] = []
    if status == UNRESOLVED:
        missing, extra = _matcher_missing(verdicts, RISK_TIER_SCOPES, decide, b)
        evidence += [e for e in extra
                     if e.logical_provision_id not in {x.logical_provision_id for x in evidence}]
        if not missing:
            raise AdapterError("risk tier unresolved but no decisive missing input")
    applicable_from = None
    if reason == "not_yet_applicable":
        applicable_from = min(engine.route_start(ROUTE_PROVISION[c])
                              for c in tier.system_category)

    def route(state) -> str:
        return {True: "established", False: "not met", None: "undetermined"}[state]

    explanation = (
        f"Annex III route (Article 6(2)): {route(tier.routes.get(ANNEX_III))}; "
        f"product route (Article 6(1)): {route(tier.routes.get(ARTICLE_6_1))}; "
        f"Article 6(3) derogation: {tier.derogation or 'undetermined'}; "
        f"matched provisions: "
        f"{', '.join(short_citation(b.provisions[p]) for p in sorted(tier.matched) if scope_of(p) in RISK_TIER_SCOPES) or 'none'}"
    )
    return Determination(RISK_TIER, RISK_TIER, status, reason, explanation,
                         tuple(evidence), tuple(missing), applicable_from=applicable_from,
                         system_category=tuple(tier.system_category),
                         derogation=tier.derogation)


def _prohibited(det: PipelineDetermination, b: _Builder) -> Determination:
    status, reason = split_status(det.prohibited.status)
    points = sorted(pid for pid in b.provisions if pid.startswith(ARTICLE_5_POINTS))
    if status == NOT_IN_FORCE:
        return Determination(PROHIBITED, PROHIBITED, status, None,
                             "no Article 5 text was in force on the date",
                             in_force_from=b.in_force_from(points))
    verdicts = det.match.verdicts
    engine = b.pipeline.engine

    def decide(vs):
        return prohibited_practice(vs, b.on, engine).status

    matched = sorted(det.prohibited.points)
    missing: list[MissingInput] = []
    extra: list[EvidenceItem] = []
    if status == UNRESOLVED:
        missing, extra = _matcher_missing(verdicts, ("article_5",), decide, b)
        if not missing:
            raise AdapterError("prohibited practice unresolved but no decisive missing input")
    cited = {e.logical_provision_id for e in extra}
    evidence = [b.evidence(pid) for pid in matched if pid not in cited]
    # The points assessed and not matched are cited, without text, so a negative
    # conclusion still has citable evidence. Their verdicts stay out (§16.2).
    evidence += [b.evidence(pid, with_excerpt=False) for pid in points
                 if pid not in matched and pid not in cited]
    evidence += extra
    applicable_from = None
    if reason == "not_yet_applicable":
        applicable_from = min(r.applicable_from for pid in matched
                              for r in engine.rules[pid])
    explanation = ("matched Article 5(1) points: "
                   f"{', '.join(short_citation(b.provisions[p]) for p in matched) or 'none'}")
    return Determination(PROHIBITED, PROHIBITED, status, reason, explanation,
                         tuple(evidence), tuple(missing), applicable_from=applicable_from)


def _fact_question(name: str) -> str:
    description = CaseFacts.model_fields[name].description or name
    return description.split(" null ")[0].strip()


def _roles(det: PipelineDetermination, facts: CaseFacts, b: _Builder) -> list[Determination]:
    out = []
    roles = det.roles
    for role in ("provider", "deployer"):
        did = f"{ACTOR_ROLE}:{role}"
        if roles is None:
            ids = ["aiact:art_3:point_3" if role == "provider" else "aiact:art_3:point_4"]
            out.append(Determination(did, ACTOR_ROLE, NOT_IN_FORCE, None,
                                     "roles are not determined before entry into force",
                                     in_force_from=b.in_force_from(ids)))
            continue
        value = roles.values[role]
        status, reason = {True: (APPLICABLE, None), False: ("not_applicable", "conditions_not_met"),
                          None: (UNRESOLVED, None)}[value]
        evidence = [b.evidence(pid) for pid in roles.evidence[role]]
        missing: list[MissingInput] = []
        if status == UNRESOLVED:
            for name in ROLE_FACTS[role]:
                if getattr(facts, name) is None:
                    missing.append(MissingInput("case_fact", name, _fact_question(name)))
            if role == "provider" and facts.develops_system is not True:
                if det.risk_tier.high_risk is None:
                    missing.append(MissingInput(
                        "upstream_determination", RISK_TIER,
                        "depends on the risk tier, which is itself unresolved"))
                if det.risk_tier.high_risk is not False:
                    for name in TRANSFER_FACTS:
                        if getattr(facts, name) is None:
                            missing.append(MissingInput("case_fact", name, _fact_question(name)))
            if not missing:
                raise AdapterError(f"{role} unresolved but no missing input found")
        explanation = (
            f"{role}: {'yes' if value else 'no' if value is False else 'undetermined'}"
            + (f"; substantial modification: {roles.substantial_modification}"
               if role == "provider" else ""))
        out.append(Determination(did, ACTOR_ROLE, status, reason, explanation,
                                 tuple(evidence), tuple(missing)))
    return out


def _obligations(det: PipelineDetermination, b: _Builder) -> list[Determination]:
    out = []
    engine = b.pipeline.engine
    for group in OBLIGATION_GROUPS:
        did = f"obligation:{group}"
        statuses = det.obligations[group]
        group_status = det.group_status(group)
        if group_status == "mixed":
            raise AdapterError(f"{group}: provisions disagree; not choosing a status")
        status, reason = split_status(group_status)
        pids = [s.logical_provision_id for s in statuses]
        if status == NOT_IN_FORCE:
            out.append(Determination(did, OBLIGATION, status, None,
                                     f"no {group} text was in force on the date",
                                     in_force_from=b.in_force_from(pids)))
            continue
        with_text = status == APPLICABLE or reason == "not_yet_applicable"
        evidence = [b.evidence(pid, with_excerpt=with_text) for pid in pids]
        missing: list[MissingInput] = []
        if status == UNRESOLVED:
            refs: set[str] = set()
            for s in statuses:
                for o in s.rule_outcomes:
                    if o.outcome is not None:
                        continue
                    rule = next(r for r in engine.rules[s.logical_provision_id]
                                if r.rule_id == o.rule_id)
                    role = rule.conditions["actor_role"]
                    if role != "any" and det.roles and det.roles.values.get(role) is None:
                        refs.add(f"{ACTOR_ROLE}:{role}")
                    category = rule.conditions["system_category"]
                    if category != "any" and det.risk_tier.routes.get(category, False) is None:
                        refs.add(RISK_TIER)
            for ref in sorted(refs):
                label = "the risk tier" if ref == RISK_TIER else f"the {ref.split(':')[1]} role"
                missing.append(MissingInput("upstream_determination", ref,
                                            f"depends on {label}, which is itself unresolved"))
            if not missing:
                raise AdapterError(f"{group} unresolved but no upstream unknown found")
        applicable_from = None
        if reason == "not_yet_applicable":
            starts = [r.applicable_from for s in statuses for o in s.rule_outcomes
                      if o.outcome == NA_NOT_YET
                      for r in engine.rules[s.logical_provision_id] if r.rule_id == o.rule_id]
            applicable_from = min(starts)
        outcomes = sorted({str(o.outcome) for s in statuses for o in s.rule_outcomes})
        explanation = f"{group}: rule outcomes {', '.join(outcomes) or 'none'}"
        out.append(Determination(did, OBLIGATION, status, reason, explanation,
                                 tuple(evidence), tuple(missing),
                                 applicable_from=applicable_from))
    return out


def adapt(det: PipelineDetermination, facts: CaseFacts, on: date, corpus: Corpus,
          pipeline: Pipeline, case_ref: str) -> GenerationInput:
    b = _Builder(corpus, pipeline, on)
    determinations = [_risk_tier(det, b), _prohibited(det, b), *_roles(det, facts, b),
                      *_obligations(det, b)]
    for d in determinations:
        if (d.applicability_status == UNRESOLVED) != bool(d.unresolved_inputs):
            raise AdapterError(f"{d.determination_id}: unresolved_inputs iff unresolved")
        if d.applicability_status == NOT_IN_FORCE and d.supporting_evidence:
            raise AdapterError(f"{d.determination_id}: not_in_force carries no evidence")
        if (d.not_applicable_reason is not None) != (d.applicability_status == "not_applicable"):
            raise AdapterError(f"{d.determination_id}: reason set iff not_applicable")
    return GenerationInput(case_ref, on, tuple(determinations))


__all__ = [
    "ACTOR_ROLE", "APPLICABLE", "AdapterError", "Determination", "EvidenceItem",
    "GenerationInput", "MissingInput", "NA_CONDITIONS", "NA_NOT_YET", "NOT_IN_FORCE",
    "OBLIGATION", "PROHIBITED", "RISK_TIER", "UNRESOLVED", "adapt", "replay",
    "replay_synthetic", "short_citation", "split_status",
]

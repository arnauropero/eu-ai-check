# PROJECT_PLAN.md — EU AI Act Compliance Determination System

Status: V1 scope locked. Companion document to SYSTEM_SPEC.md (technical source of
truth). This document governs scope, milestones, and portfolio narrative.

---

## 1. Problem Statement

Determining whether the EU AI Act applies to a given AI system, and what it requires,
is not a lookup problem — it depends on the system's purpose and functionality, who is
operating it and in what role, and when it's being assessed against a regulation with a
staggered, multi-year application schedule. A naive "chat with the AI Act" RAG system
answers questions about the text; it does not correctly bind obligations to a specific
case, and it has no way to represent "I don't have enough information to determine
this" versus "this doesn't apply."

## 2. Project Objective

Build a system that takes a free-text description of an AI use case and produces a
grounded, citable compliance determination: risk tier, applicable obligations by actor
role, explanation, and evidence — with retrieval and rule-evaluation architecture
capable of handling text that changes over time and obligations that become binding on
different schedules.

Primary objective is portfolio signal for AI/ML engineering roles: demonstrating
production-grade RAG engineering (hybrid retrieval, temporal correctness, grounded
determination, fail-closed error handling) on a domain with genuinely hard retrieval
problems — not another "chat with your PDFs" demo.

## 3. V1 Scope

- Single regulation: EU AI Act only.
- Free-text case input → structured extraction → hybrid retrieval → grounded
  determination → cited output.
- Risk-tier classification against Annex III (`high_risk` / `not_high_risk`).
- Prohibited-practice determination against Article 5, represented as its own
  determination type (`prohibited_practice`), evaluated via the `ApplicabilityRule`
  engine where applicable-date conditions apply.
- Actor-role determination (provider / deployer / unknown), including the Article 25
  role-transfer check (substantial modification, own-branding).
- Obligation applicability for Article 16 (provider) and Article 26 (deployer)
  obligation sets, evaluated via the `ApplicabilityRule` engine.
- Full temporal architecture: text versioning + applicability rules, evaluated
  independently, per SYSTEM_SPEC.md §13–14.
- Four-way applicability outcome (`applicable / not_applicable / unresolved /
  not_in_force`) surfaced in output, not just logged internally.
- Retrieval evaluation harness comparing vector-only vs. hybrid retrieval.

## 4. Explicit Non-Goals (V1)

- **Multi-regulation support.** GDPR overlap is a known, real interaction (e.g. FRIA
  and DPIA overlap) but is explicitly deferred — adding it now doubles the domain-
  modeling surface before the single-regulation case is proven.
- **Arbitrary obligation splitting / first-class `Obligation` entity.** Deferred until
  corpus evidence (not speculation) shows the provision-level model is insufficient —
  see the deferred-abstraction principle in §9.
- **Multi-hop cross-reference traversal.** Single-hop resolution only in V1; stretch
  goal.
- **Full role coverage.** Importer, distributor, authorised-representative roles exist
  in the Act (Article 3) but are out of scope for V1, which covers provider/deployer
  only. This is a scoping decision, documented explicitly rather than left as an
  unstated gap.
- **Legal advice.** The system produces a grounded determination with citations, not a
  substitute for legal counsel; this framing is explicit in any user-facing output and
  the README.
- **Freeform, unstructured legal reasoning.** The system is bounded to the Act's own
  risk-tier and obligation structure — it is a classification-and-retrieval problem
  with a defensible ground truth, not open legal interpretation.

## 5. Architecture Overview

See SYSTEM_SPEC.md in full. Summary: free-text extraction (facts only) → hybrid
retrieval with hard temporal pre-filtering → parent-document resolution → three
`GroundedDetermination` stages (risk tier, actor role, obligation applicability) sharing
one rule-matching engine → cited, fail-closed output.

## 6. Implementation Phases and Milestones

**Phase 0 — Corpus ingestion and validation (current → next)**
- Deliverable: ingested Article 5, 16, 25, 26, and Annex III as structured
  `Provision` records with `structural_location` paths, `content_id` deduplication, and
  populated `text_valid_from/to`.
- Deliverable: ingestion-time validation check enforcing the non-overlap invariant
  (SYSTEM_SPEC.md §11).
- Deliverable: ingested `ApplicabilityRule` records for the V1 corpus (Article 5, 16,
  26 — the provisions carrying applicability conditions in V1), covering the AI Act's
  staggered application schedule (SYSTEM_SPEC.md §12).
- Deliverable: ingestion-time validation of `ApplicabilityRule` temporal fields
  (`applicable_from`/`applicable_to`), `conditions`/`scope` content, and each rule's
  `legal_provision_id` reference against an ingested `Provision` (SYSTEM_SPEC.md
  §12.1).
- Acceptance: every provision citable at its correct granularity; Annex III Area 2
  terminal case handled correctly; Article 5(1)(h) nested structure preserved intact as
  one canonical provision; every ingested `ApplicabilityRule` passes temporal-field and
  provision-reference validation, with no rule left dangling or unvalidated.
- Deliverable: initial measured baselines for the metric categories in
  SYSTEM_SPEC.md §18 (retrieval recall/precision, extraction accuracy) using early
  ingested data — establishing that numbers exist to set targets against. Numerical
  targets themselves are locked progressively through Phase 0–1, not invented upfront.

**Phase 1 — Retrieval layer**
- Deliverable: hybrid search (semantic + BM25) over ingested provisions, with hard
  pre-retrieval temporal filtering and reranking.
- Deliverable: parent-document resolution from matched sub-chunk to canonical
  provision.
- Deliverable: retrieval evaluation harness (hand-built query set with known-correct
  provisions) comparing vector-only vs. hybrid, with vs. without temporal filtering.
- Acceptance: hybrid retrieval beats vector-only on Recall@k and Precision@k
  (SYSTEM_SPEC.md §18) by a margin locked as part of this phase's baseline-setting
  work, not assumed in advance; the comparison must be demonstrated specifically on
  queries using precise legal terminology (article numbers, Annex III category names)
  where BM25 is expected to matter most.

**Phase 2 — Extraction stage**
- Deliverable: schema-constrained extraction from free text to the `Case` schema
  (SYSTEM_SPEC.md §3.2).
- Deliverable: extraction eval set (hand-labeled free-text → expected fields).
- Acceptance: field-level accuracy meets the target locked during this phase against
  the Phase 0 baseline (SYSTEM_SPEC.md §18); extraction **never** outputs a value for
  an excluded (conclusion-level) field — this is a zero-tolerance check, not a
  threshold.

**Phase 3 — Grounded determination stages**
- Deliverable: `GroundedDetermination` component implemented once, parameterized for
  risk-tier, prohibited-practice, actor-role, and obligation-applicability use.
- Deliverable: `ApplicabilityRule` matching engine (the evaluation logic; the rule
  records themselves are ingested and validated in Phase 0, SYSTEM_SPEC.md §12.1),
  used only where a determination's legal question requires date-scoped or
  conditional applicability evaluation (obligation applicability and
  prohibited-practice determination) — not a mandatory layer for every
  `GroundedDetermination` instantiation (SYSTEM_SPEC.md §12).
- Deliverable: actor-role determination producing `actor_roles: list` +
  `actor_role_status`, with independently evaluated, unioned obligation sets for
  simultaneous roles (SYSTEM_SPEC.md §4.1).
- Deliverable: determination eval set explicitly including `unresolved` and
  `not_in_force` cases, not just `applicable`/`not_applicable` pairs — and at least one
  case exercising simultaneous provider+deployer roles.
- Acceptance: four-way outcome (`applicable`/`not_applicable`/`unresolved`/
  `not_in_force`) correctly produced on eval set; `unresolved` never silently collapsed
  to `not_applicable`; `not_in_force` never silently collapsed to `not_applicable`
  (SYSTEM_SPEC.md §14).

**Phase 4 — Generation and citation**
- Deliverable: final generation stage producing tier + obligations + explanation +
  citations from the evidence set.
- Acceptance: every claim in generated output traceable to a specific
  `versioned_provision_id`; no obligation stated without a citation.

**Phase 5 — End-to-end evaluation and polish**
- Deliverable: end-to-end eval set with full expected outputs.
- Deliverable: README documenting architecture, rejected alternatives, and known
  limitations (including the Digital Omnibus amendment to Article 5 as a live
  versioning example).

## 7. Evaluation Plan

Per-stage evaluation as detailed in SYSTEM_SPEC.md §18: extraction accuracy, retrieval
recall/precision (vector-only vs. hybrid, with/without temporal filtering), 
determination correctness (including `unresolved` coverage), and end-to-end scoring
against manually determined expected outputs. Evaluation sets are themselves a
deliverable (§10), not an afterthought.

## 8. Acceptance Criteria (V1 complete when)

- All five phase-level acceptance criteria above are met.
- The four-way `applicable/not_applicable/unresolved/not_in_force` output is
  demonstrably exercised by real eval cases (all four states, not just the easy ones),
  not just theoretically possible.
- Numerical targets for every metric category in SYSTEM_SPEC.md §18 have been locked
  (not left as "TBD") against Phase 0 baselines, and the implemented system meets them.
- At least one eval case explicitly demonstrates correct handling of the Article 25
  role-transfer scenario (deployer becoming subject to provider obligations).
- At least one eval case explicitly demonstrates simultaneous provider+deployer roles,
  with correctly unioned and deduplicated obligations (SYSTEM_SPEC.md §4.1).
- At least one eval case explicitly demonstrates correct temporal behavior (same
  provision, two different `relevant_date`s, two different applicability outcomes).
- At least one eval case explicitly demonstrates the `not_in_force` vs. `not_applicable`
  distinction on the same provision at two different dates.
- README documents the architecture decisions and rejected alternatives at a level
  sufficient to defend them in an interview without re-deriving them live.

## 9. Engineering Practices

- **Defer abstractions until corpus evidence justifies them.** The clearest instance:
  no first-class `Obligation` entity in V1. The provision-level model was tested
  against Article 16 (already atomic) and against the Article 5(1)(h) bundled-exception
  case before concluding it holds; the decision was evidence-based, not
  under-engineered. Revisit only if ingestion of the full corpus shows frequent
  unsplittable multi-obligation provisions.
- **Fail closed, not silent.** Every stage with a "we don't know" outcome
  (`unresolved` applicability, missing cross-reference target, extraction low-
  confidence) surfaces that state rather than defaulting to a plausible-looking answer.
- **One mechanism, parameterized — not N bespoke mechanisms.** `GroundedDetermination`
  and `ApplicabilityRule` matching are each implemented once and reused, not
  duplicated per use case.
- **Validate assumptions against the real corpus before locking design**, not after
  implementation. Demonstrated in Phase 0 by checking Article 5/16/26/Annex III
  structure before finalizing the chunking model — this caught the fixed-depth
  structural-path assumption before it became a schema migration.

## 10. Data / Evaluation Assets to Create

- Retrieval eval query set with known-correct `versioned_provision_id` ground truth,
  including cases specifically designed to surface candidate starvation without
  temporal pre-filtering.
- Extraction eval set: free-text case descriptions with hand-labeled expected
  structured fields.
- Determination eval set covering all four applicability outcomes, including at least
  one deliberately under-covered rule scenario to produce a genuine `unresolved` case
  (not a synthetic one).
- End-to-end eval set with full expected output (tier, obligations, citations) for a
  representative spread of use cases across Annex III areas.

## 11. Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Vector DB's physical filtered-search behavior doesn't match the logical pre-filtering design under realistic selectivity | Benchmark early (Phase 1), before committing to a specific DB; flagged as an open question in SYSTEM_SPEC.md §21. |
| Corpus ingestion reveals structural irregularities beyond what Phase 0 validation caught | Phase 0 is explicitly a validation phase before Phase 1 retrieval work begins, precisely to surface this early. |
| Scope creep toward multi-regulation or full role coverage | Non-goals (§4) are documented explicitly and referenced at each phase boundary. |
| `ApplicabilityRule` referencing `logical_provision_id` proves insufficient once real Article 113 phase-in dates are ingested | Flagged as an open question (SYSTEM_SPEC.md §21); revisit during Phase 0 ingestion of applicability data, not deferred silently. |
| Extraction step drifts toward outputting legal conclusions under prompt pressure (e.g. model infers risk tier during extraction) | Extraction eval set explicitly tests for this failure mode; schema enforcement rejects unexpected fields. |

## 12. Stretch Goals

- Multi-hop cross-reference traversal.
- `obligation_id` entity, if corpus evidence justifies it.
- Multi-jurisdiction support.
- `relevant_date` field-splitting (`event_date`/`assessment_date`/`deployment_date`).
- Incorporating the Digital Omnibus amendment to Article 5 once it has a confirmed
  effective date.

## 13. Portfolio / Interview Narrative

The project's strongest signal is not the final pipeline, but the trajectory: starting
from "build a RAG chatbot for the AI Act" and, through deliberate design pressure-
testing, arriving at a versioned, temporally-aware, fail-closed evidence resolution
system with a reusable grounded-determination pattern applied across five distinct
legal conclusions. Interview-ready talking points, each backed by a concrete design
decision in SYSTEM_SPEC.md:

- Why naive chunking breaks on legal text, and what parent-document (small-to-big)
  retrieval solves — grounded in a real example (Article 5(1)(h)), not a hypothetical.
- Why "the text is valid" and "the obligation applies" are different questions, and
  what breaks if you conflate them.
- Why temporal filtering must be a pre-retrieval hard constraint, with the candidate-
  starvation failure mode as the concrete justification.
- Why compute-time and index-time deduplication are different concerns that get
  conflated by default, and what breaks if you don't separate them.
- Why deferring the `Obligation` entity was a corpus-evidence-based decision, not
  under-engineering — and what would change the decision.

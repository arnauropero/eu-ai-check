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
- Risk-tier classification against **Article 6** and Annex III (`high_risk` /
  `not_high_risk`), covering both high-risk routes (Article 6(1) product-embedded,
  Article 6(2) Annex III) and the Article 6(3) derogation under which an Annex III
  system is *not* high-risk.
  **[Scope extended on Phase 0 corpus evidence, 2026-10-01]** Article 6 was not in
  the original V1 list. Phase 0 ingestion established it as a hard dependency
  rather than a nice-to-have: 27 of the 73 provisions ingested without it
  cross-referenced it (overall in-corpus cross-reference resolution was 9.1%), and
  the `ApplicabilityRule` conditions on Articles 16 and 26 distinguish
  `annex_iii_high_risk` from `article_6_1_high_risk` — a distinction Article 6 is
  what defines. Without it the rule layer depended on a concept whose defining
  provision was absent. This is the deferred-abstraction principle in §9 working as
  intended: the scope gap was found by corpus evidence, not speculation.
  **[Scope extended in Phase 3 design, 2026-10-03]** **Annex I** joins the corpus for
  the same reason. Article 6(1) is defined by reference to the legislation listed
  there (SYSTEM_SPEC.md §9.1). **Article 3, points (3), (4) and (23) only** (the
  provider, deployer and substantial-modification definitions) joins too, to ground
  the actor-role rules (SYSTEM_SPEC.md §4.3).
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
  unstated gap. Ingesting Article 6 brought in duties on a further actor — the
  Commission (Article 6(5)–(7): issuing guidelines, adopting delegated acts). These are
  ingested as `obligation` provisions carrying an explicit
  `applicability_gap_reason`, so the §12.1 no-orphan check reports them as *declared*
  gaps rather than failing ingestion or silently passing. The omission is therefore
  reviewable in the validation output instead of invisible.
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

**Phase 0 — Corpus ingestion and validation (COMPLETE, 2026-10-01)**
- Deliverable: ingested Article 5, 6, 16, 25, 26, and Annex III as structured
  `Provision` records with `structural_location` paths, `content_id` deduplication, and
  populated `text_valid_from/to`.
  **Delivered:** 85 canonical provisions and 76 `ApplicabilityRule` records from the
  official OJ XHTML (CELEX 32024R1689, EU Publications Office Cellar API — EUR-Lex
  itself is behind an AWS WAF challenge that blocks scripted fetches). 48 tests pass
  against the real text; all invariants hold.
- Deliverable: ingestion-time validation check enforcing the non-overlap invariant
  (SYSTEM_SPEC.md §11).
- Deliverable: ingested `ApplicabilityRule` records for the V1 corpus (Article 5, 6,
  16, 25, 26 — the provisions carrying applicability conditions in V1), covering the
  AI Act's staggered application schedule (SYSTEM_SPEC.md §12).
  **Delivered:** every date transcribed from Article 113 with the point it came from
  recorded in `source_reference`. Article 113(c) turned out to force *two* rules per
  high-risk obligation rather than one date — the same Article 16 text starts
  applying 2026-08-02 for Annex III systems and 2027-08-02 for Article 6(1) systems.
  Article 6(1) and 6(2) carry their own application dates too, so a determination can
  distinguish "not high-risk" from "that classification route was not yet in force".
- Deliverable: ingestion-time validation of `ApplicabilityRule` temporal fields
  (`applicable_from`/`applicable_to`), `conditions`/`scope` content, and each rule's
  `legal_provision_id` reference against an ingested `Provision` (SYSTEM_SPEC.md
  §12.1).
- Acceptance: every provision citable at its correct granularity; Annex III Area 2
  terminal case handled correctly; Article 5(1)(h) nested structure preserved intact as
  one canonical provision; every ingested `ApplicabilityRule` passes temporal-field and
  provision-reference validation, with no rule left dangling or unvalidated.
  **Met.** Each clause above has a test named after it. Three hazards in the real
  text, not anticipated by this plan, are now covered by tests: (1) marker text is
  ambiguous across levels — Article 16 has a lettered point "(i)" and Article 5(1)(h)
  a roman sub-point "(i)" — so level is decided by tree position, never by parsing the
  marker; (2) Article 5(1)'s closing savings clause sits *after* its (a)–(h) list and
  qualifies point (h) only, so a node's text and children must be stored in one
  ordered sequence or the clause is re-emitted before the list it qualifies; (3) a
  canonical provision needs its ancestor lead-in — Article 5(1)(a) read alone is a
  bare noun phrase, the operative verb is in the parent.
- Deliverable: initial measured baselines for the metric categories in
  SYSTEM_SPEC.md §18 (retrieval recall/precision, extraction accuracy) using early
  ingested data — establishing that numbers exist to set targets against. Numerical
  targets themselves are locked progressively through Phase 0–1, not invented upfront.
  **Delivered** via `eu-ai-check baseline`, which reports the corpus-side numbers that
  can honestly be measured now (granularity and depth distribution, canonical text
  size, `content_id` dedup rate, rule coverage, cross-reference resolution) and names
  each deferred §18 category with the phase component that unblocks it, rather than
  inventing a number for it. Phase 1 input worth noting: canonical text runs 90 / 418
  / 2726 chars (min/median/max) and exactly one provision exceeds 2000 chars — Article
  26(10), which would overrun a 512-token embedding window if embedded whole.
  **[Corrected in Phase 1, 2026-10-02]** This note originally named Article 5(1)(h)
  as the longest provision. It is 1,191 chars. Parent resolution is still mandatory
  for 5(1)(h), but the reason is structural, not length: one of its sub-chunks
  states exception (iii) without the prohibition it excepts from. A test now pins
  the longest provision so the claim cannot silently drift again.

**Phase 1 — Retrieval layer (COMPLETE, 2026-10-02)**
- Deliverable: hybrid search (semantic + BM25) over ingested provisions, with hard
  pre-retrieval temporal filtering and reranking.
  **Delivered** (`src/eu_ai_check/retrieval/`, `eu-ai-check search`): temporal
  pre-filter on both retrievers → bge-base-en-v1.5 in Qdrant + citation-aware BM25 →
  reciprocal rank fusion → exact-citation priority → parent resolution. Reranking
  is implemented (bge-reranker-base) but **off by default**: it lost in eval, so it
  is kept as a measured arm only (SYSTEM_SPEC.md §7.4). Exact-citation priority was
  *not* in the original plan; it entered as an experimental arm with its matching
  rule fixed before its first run, and was adopted on the measured result.
- Deliverable: parent-document resolution from matched sub-chunk to canonical
  provision.
  **Delivered.** Provisions over 800 characters are split at sentence/enumerator
  boundaries (lossless, tested). The Article 5(1)(h) case is real: one chunk holds
  exception (iii) without the prohibition, and a match on it returns the whole
  provision (tested by name).
- Deliverable: retrieval evaluation harness (hand-built query set with known-correct
  provisions) comparing vector-only vs. hybrid, with vs. without temporal filtering.
  **Delivered** (`eu-ai-check eval-retrieval`): 98 queries scored at
  `versioned_provision_id` level; vector / BM25 / hybrid / hybrid+citation, each
  with and without reranking; pre-filter / post-filter / no filter on an eval-only
  **synthetic** version fixture (the real corpus still has one version per
  provision, so the temporal arms would tie there by construction). Candidate
  starvation shows at k = 1 (post-filter R@1 0.500 vs pre-filter 1.000) and is
  reported at k = 1 and k = 5.
- Deliverable (from §11 risk table): vector-DB filtered-search benchmark.
  **Delivered** (`eu-ai-check bench-filter`, Qdrant 1.19.1 server): HNSW filtered
  recall holds only with date payload indexes built before the graph (0.007 →
  1.000 at 1% selectivity). Now a hard index requirement, SYSTEM_SPEC.md §13.2.
- Acceptance: hybrid retrieval beats vector-only on Recall@k and Precision@k
  (SYSTEM_SPEC.md §18) by a margin locked as part of this phase's baseline-setting
  work, not assumed in advance; the comparison must be demonstrated specifically on
  queries using precise legal terminology (article numbers, Annex III category names)
  where BM25 is expected to matter most.
  **Met — with a stated caveat.** The pre-registered terminology queries could not
  test this: they carry topical words and every arm scored Recall@5 = 1.0. Bare-
  citation queries were therefore **added after the first run had been seen**
  (flagged `post_hoc`; the original 65-query run is frozen in
  `data/eval/results/baseline_v1_65q*`). On them the design beats vector-only by
  Recall@5 +0.269 (1.000 vs 0.731) and Precision@5 +0.069; on the 16 never run
  before adding, +0.250. Locked thresholds: Recall@5 +0.15 and Precision@5 +0.03
  over vector-only on bare citations, and no use-case regression. **Because both the
  queries and the thresholds were fixed after seeing results, these are regression
  guards, not pre-registered acceptance criteria**, and are presented that way
  (SYSTEM_SPEC.md §18). Known limit: other citation styles ("Art. 26(6)") get no
  citation priority; left unfixed rather than tuned to seen queries.

**Phase 2 — Extraction stage (COMPLETE, 2026-10-02)**
- LLM evaluation stack: **OpenAI only, needing only `OPENAI_API_KEY`**. The extractor
  is **GPT-6.1 Sol (`gpt-6.1-sol`)**, with effort arms `low`/`medium` and strict
  Structured Outputs. The optional secondary judge is **GPT-6 Astra (`gpt-6-astra`)**
  at effort `low`; it never defines or changes the ground truth, and `--no-judge`
  skips it. Both were switched from Claude Opus 5.5 on 2026-10-02, after
  pre-registration and before any API call, with the eval set, labels, scoring and
  judge method unchanged (SYSTEM_SPEC.md §3.5). The Claude extractor stays as an
  optional, non-evaluated provider behind the `anthropic` extra.
- Status: extractor (originally Claude Opus 5.5, structured outputs, refusal fallbacks), harness
  (`eu-ai-check eval-extraction`), conclusion-leakage check and a 30-case labelled eval
  set were committed **before the first API run**. The labels were reviewed by the user
  first, and one changed in that review (E18, documented as pre-registration, not
  post-hoc). Field rules and labelling decisions L1–L9 are in SYSTEM_SPEC.md §3.4. The
  accuracy target is locked after the first run.
- First pre-registered run (2026-10-02): overall field accuracy 0.967 (`low`) and
  0.959 (`medium`), with 0 leakage hits. Results are frozen in
  `data/eval/results/extraction_v1_30c*` and summarised in SYSTEM_SPEC.md §18. The
  accuracy target was locked afterwards, following error analysis of
  `affected_persons` and `role_in_decision_making` (see Acceptance).
- Follow-up experiment `role-null-v1` (pre-registered 2026-10-02): a prompt-only
  clarification of `role_in_decision_making` null discipline. It runs 4 arms
  ({baseline, role-null-v1} × {low, medium}, 360 calls) under a fixed decision rule
  (SYSTEM_SPEC.md §18). The frozen v1 baseline is unchanged. Adopting the prompt and
  changing the default effort are decided separately. **Result: the decision rule was
  not met, and the prompt was not adopted.** E09/E14 improved, but E27 regressed (a
  guard case) and E06 was over-corrected (SYSTEM_SPEC.md §18).
- Accepted configuration: `gpt-6.1-sol`, baseline prompt, `low` effort. This is an
  engineering choice on observed results and output-token cost, not a claim that
  `low` is more accurate. The judge is evaluation-only.
- Deliverable: schema-constrained extraction from free text to the `Case` schema
  (SYSTEM_SPEC.md §3.2).
  **Delivered** (`src/eu_ai_check/extraction/`, `eu-ai-check extract`): strict
  Structured Outputs over `CaseFacts`, pydantic validation as fail-closed final
  enforcement, and `Case.from_facts`. All 540 evaluation runs returned schema-valid
  output.
- Deliverable: extraction eval set (hand-labeled free-text → expected fields).
  **Delivered:** `data/eval/extraction_cases.json`, 30 cases, user-reviewed and
  committed before any API call (`fe0b0f6`); its content is SHA-pinned in tests.
- Acceptance: field-level accuracy meets the target locked during this phase against
  the Phase 0 baseline (SYSTEM_SPEC.md §18); extraction **never** outputs a value for
  an excluded (conclusion-level) field — this is a zero-tolerance check, not a
  threshold.
  **Met.**
  - *Accuracy:* target overall field accuracy ≥ 0.95, scored by the pre-registered
    concept-group scorer at `low` effort. Result 0.967 on the frozen v1 run
    (`82467bb`). Phase 0 had no extraction measurement, so the v1 run is the
    baseline. **The target was locked after the first results were seen**, as the
    Phase 2 pre-registration allows. It is a regression guard, not a pre-registered
    criterion.
  - *Zero tolerance:* 0 leaks in 180 v1 runs, and 0 in 360 v2 runs.
  - The post-hoc affected-persons re-score is not used for acceptance.

**Phase 3 — Grounded determination stages**
- Design decisions (2026-10-03, SYSTEM_SPEC.md §2, §4.2, §9.1):
  - Annex III / Article 5 / Article 6 matching uses one constrained LLM provision
    matcher. It sees a closed, exhaustive candidate set, returns per-candidate verdicts
    with verbatim quotes, and code verifies each verdict. A retrieval-score cutoff runs
    as the baseline arm.
  - No `Case` schema change. Phase 2 stays closed. Article 6(3) is read from the
    original description.
  - Annex I is ingested for Article 6(1).
  - Actor-role rules are written down (SYSTEM_SPEC.md §4.3): three-valued, with
    Article 25 transfer only on high-risk systems.
  - `unresolved_roles` keeps partial role knowledge.
  - Article 3(3), (4) and (23) are ingested. Corpus: 110 provisions.
- Deliverable (corpus, before any determination code): fix the dropped
  trailing-subparagraph defect (Article 6(3) profiling override, Article 5(2) second
  subparagraph), with a test that every V1 source fragment appears in some canonical
  provision. Ingest Annex I. Re-run the Phase 0 validation and record any change in
  Phase 1 retrieval numbers against the frozen results.
  **Delivered (2026-10-03):**
  - 107 provisions (was 85); the 85 existing ones are unchanged; 76 rules.
  - The two dropped subparagraphs are now their own provisions at a new subparagraph
    level.
  - Annex I is ingested: 20 entries across Sections A and B.
  - A second Phase 0 defect was found and fixed: five false internal cross-reference
    edges caused by regex backtracking.
  - All invariants hold. The Phase 1 regression guards still hold on the re-run
    (`data/eval/results/corpus_v2_107p_98q.*`; SYSTEM_SPEC.md §9.1, §18).
- Deliverable: provision matcher with code verification, plus the score-cutoff
  baseline arm, compared under a pre-registered decision rule (SYSTEM_SPEC.md §18).
  **Pre-registered (2026-10-03), before any determination run:**
  - 41-case eval set (`9cb9bd2`).
  - Determination code, matcher prompt (SHA-pinned) and τ values (next commit).
  - The rule path reproduces all 123 rule-based labels offline.
  - **First run (2026-10-04): acceptance NOT MET** (SYSTEM_SPEC.md §18). 26 of 123
    calls failed with connection errors and only those were retried. On the merged
    run, M-low beats the baseline on both macros but fails zero tolerance (D32) and
    the per-state targets on `not_applicable`. Prohibited is 0.632, mostly from
    Article 5(1)(c) over-caution. Risk tier is 0.786, from D10, D26 and D28.
  - **Post-hoc 5(1)(c) intervention: NOT ADOPTED.** The 41 cases became a dev set, and
    an 8-case held-out set was frozen first (never run). Two prompt variants were
    tried. `social-scoring-v1` fixed 5(1)(c) but spilled over (D32, D24).
    `social-scoring-v2`, scoped to the 5(1)(c) candidate, still produced
    zero-tolerance violations on D32. The user's hard stop ended the intervention.
    The pre-registered prompt stays the default.
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
  **"Correctly produced" defined before the first run (2026-10-03,
  SYSTEM_SPEC.md §18):**
  - Scored on gold `Case` inputs.
  - Rule-based outputs (actor role, obligations, `not_in_force`, `not_applicable`
    reason) must score 1.00.
  - Matcher-backed outputs (risk tier, prohibited practice) need per-state accuracy
    ≥ 0.85 on each of `applicable` / `not_applicable` / `unresolved`, and the matcher
    must beat the score-cutoff baseline.
  - Zero-tolerance checks as before, plus: no positive conclusion from a verdict that
    failed code verification.
  - These are pre-registered, unlike the Phase 1–2 targets.
- Phase-in dates and unknown inputs (SYSTEM_SPEC.md §14.2):
  - A date before a rule's `applicable_from` gives `not_applicable` with
    `not_applicable_reason: not_yet_applicable`, never `not_in_force`.
  - Unresolved upstream determinations feed the rule engine as unknown, using
    three-valued logic.

**Phase 4 — Generation and citation**
- Deliverable: final generation stage producing tier + obligations + explanation +
  citations from the evidence set.
- Design approved 2026-10-06 (`docs/phase4_design.md`; SYSTEM_SPEC.md §16.1–16.3, §18):
  - Deliverable: a deterministic adapter from the replayed Phase 3 `Determination`
    to the §16 contract (`GenerationInput`).
  - Deliverable: a claims-first generator (strict JSON). Code renders the status
    lines and citations. Model: `gpt-6.1-sol` at `low`, configurable.
  - Deliverable: a template-only renderer (no LLM). It is the baseline and the
    fallback.
  - Deliverable: deterministic checks C0–C8 and an `eu-ai-check eval-generation`
    harness.
  - Developed on cached determinations only, with no pipeline re-run. The dev /
    held-out split is frozen in `data/eval/generation_split.json`.
- Acceptance: every claim in generated output traceable to a specific
  `versioned_provision_id`; no obligation stated without a citation.
  **Pre-registered 2026-10-06, before any Phase 4 API call (SYSTEM_SPEC.md §18):**
  - Every shipped answer passes checks C0–C8 (1.00).
  - The input status is reproduced in 100% of held-out records whose determination
    differs from gold.
  - The LLM generator is adopted over the template only if, on the held-out set:
    - it reaches a raw first-attempt pass rate ≥ 0.95;
    - fallback is ≤ 0.05;
    - there are zero status-changing (C4) and zero `not_in_force`-as-`not_applicable`
      (C8) violations;
    - grounded-explanation coverage is ≥ 0.90;
    - it is preferred in ≥ 15/20 blind pairwise comparisons with the template
      (frozen rating sample, sealed A/B key; ties go to the template).
    Otherwise the template ships as the Phase 4 generator.
  - Hard rule: the held-out pass runs exactly once, and nothing changes after it
    has been looked at.
  - Spend cap: $5 for all Phase 4 API calls, with held-out reserved first.
  - H01–H08 are not used in Phase 4. They are reserved for Phase 5.

**Phase 5 — End-to-end evaluation and polish**
- Deliverable: end-to-end eval set with full expected outputs.
- Deliverable: a pre-registered comparison of generation models (`gpt-6.1-sol` vs
  cheaper models). It uses the Phase 4 checks and decision rule, and is not decided
  in Phase 4.
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
- At least one eval case explicitly demonstrates the Article 6(3) derogation: a system
  listed in Annex III that is nonetheless **not** high-risk because it does not pose a
  significant risk of harm. Without this the risk-tier determination collapses to
  "appears in Annex III ⇒ high_risk", which is not what the Act says.
- At least one eval case explicitly distinguishes the two high-risk routes (Article
  6(1) product-embedded vs Article 6(2) Annex III), since they carry different
  application dates (2027-08-02 vs 2026-08-02).
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
- **A phase tag means its acceptance criteria were met.** Each phase is merged to
  `master` through a pull request with a merge commit, so the branch history stays
  intact. Only a phase whose acceptance criteria were met is tagged `phase-N`. A
  phase that ends with a documented not-met verdict is still merged, because its
  code and findings are real and final, but it is not tagged. Its verdict is
  recorded in the relevant SYSTEM_SPEC.md section. Phase 3 is the first example:
  merged in `d7d45ad`, untagged, with the NOT MET verdict in §18.

## 10. Data / Evaluation Assets to Create

- Retrieval eval query set with known-correct `versioned_provision_id` ground truth,
  including cases specifically designed to surface candidate starvation without
  temporal pre-filtering.
  **Delivered in Phase 1:** `data/eval/retrieval_queries.json` (98 queries, 33 of them
  flagged `post_hoc`) plus the synthetic version fixture
  `data/eval/synthetic_versions.json`. Lesson recorded for Phases 2–5: commit an eval
  set before running it, so its results can count as pre-registered.
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
| Vector DB's physical filtered-search behavior doesn't match the logical pre-filtering design under realistic selectivity | Benchmark early (Phase 1), before committing to a specific DB; flagged as an open question in SYSTEM_SPEC.md §21. **Done (Phase 1):** on Qdrant 1.19.1, filtered HNSW recall collapses without date payload indexes (0.007 at 1% selectivity) and holds with them; indexes-before-graph is now a hard requirement (SYSTEM_SPEC.md §13.2). |
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

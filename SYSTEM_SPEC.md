# SYSTEM_SPEC.md — EU AI Act Compliance Determination System

Status: V1 architecture locked. **Phase 0 (corpus ingestion and validation) is
implemented as of 2026-10-01; Phase 1 (retrieval) and Phase 2 (extraction) as of
2026-10-02**. Phase 2 accepted `gpt-6.1-sol` with the baseline prompt at `low` effort
against a target locked after the first run (§18). Phase 1 changed the retrieval design in three measured
places — exact-citation priority (§7.4), reranker off by default (§7.4), and date
payload indexes as a hard index requirement (§13.2) — and closed the §21
filtered-HNSW open question. **Phase 3 design (2026-10-03)** added the provision
matcher (§2, §4.2), the Article 6 route combination and Annex I (§9.1), and found a
corpus defect: trailing paragraph-level text was dropped (§9.1). **Phase 4 design
(2026-10-06)** records what Phase 3 actually outputs (§16.1), then defines the
generation input and output (§16.2–16.3) and the generation checks (§18). This document is the
technical source of truth for implementation in Claude Code. Where a decision rests on
assumption rather than verified source material, it is marked **[ASSUMPTION]**. Where a
decision has been checked against the actual EUR-Lex / AI Act Explorer text, it is marked
**[VERIFIED]**.

Phase 0 changed the architecture in exactly one place: **Article 6 was added to the V1
corpus** on corpus evidence (§9). Every other invariant, identity, and structural
decision in this document survived contact with the real Official Journal text unchanged.
The corpus was 85 canonical provisions and 76 `ApplicabilityRule` records at the end of
Phase 0 (107 provisions after the Phase 3 corpus fixes and Annex I, §9.1), built from
CELEX 32024R1689 retrieved via the EU Publications Office Cellar API — note that EUR-Lex
itself sits behind an AWS WAF JavaScript challenge that blocks scripted fetching.

Three hazards in the real text were not anticipated by this document and are now
enforced by tests. They are recorded here because each one silently corrupts legal text
rather than failing loudly: (1) enumerator markers are ambiguous across levels — Article
16 has a lettered point "(i)" and Article 5(1)(h) a roman sub-point "(i)" — so a
provision's level must be derived from its position in the parsed tree, never from
parsing its marker; (2) a node's own text and its enumerated children must be stored in
one **ordered** sequence, because Article 5(1) closes with a subparagraph that follows
its (a)–(h) list and qualifies point (h), and separate buckets re-emit it before the list
it qualifies; (3) a canonical provision must carry its ancestor lead-in, since Article
5(1)(a) read alone is a bare noun phrase whose operative verb ("The following AI
practices shall be prohibited:") lives in the parent — see §5.

---

## 1. System Goals

The system takes a free-text description of an AI use case and produces a grounded,
citable compliance determination under the EU AI Act: risk tier, applicable actor-role
obligations, and supporting evidence — not a general-purpose "chat with the AI Act"
assistant.

The core engineering problem is not "retrieve text similar to the query." It is:
correctly bound *what applies, to whom, and as of when*, using retrieval and rule
evaluation that never overstates confidence and never silently drops uncertainty.

Non-goals for the system as a whole (see PROJECT_PLAN.md §Non-Goals for the full list):
this is not a legal-advice product, not a multi-regulation platform in V1, and not a
system that performs open-ended legal reasoning outside the Act's own risk-tier
structure.

---

## 2. End-to-End Pipeline

```
Free-text use case
        │
        ▼
Fact extraction (LLM, schema-constrained)
        │  → raw case facts only, no legal conclusions
        ▼
Retrieval (hybrid: semantic + BM25 + metadata/temporal filter + reranking)
        │  → date-eligible, structurally-relevant canonical provisions
        ▼
Grounded determination (GroundedDetermination pattern, 4 top-level stages):
        │  1. Risk-tier classification (Article 6 routes: Annex III matching,
        │     Article 6(3) derogation, Article 6(1) / Annex I matching)
        │  2. Prohibited-practice determination (Article 5 matching, via
        │     ApplicabilityRule where applicable-date conditions apply)
        │  3. Actor-role determination — internally invokes a nested
        │     substantial-modification determination when Article 25
        │     role-transfer facts are present
        │  4. Obligation applicability (ApplicabilityRule matching)
        │  (5 total GroundedDetermination instantiations when the nested
        │   call is counted separately — see §4)
        ▼
Evidence resolution (parent-provision resolution, cross-reference traversal)
        ▼
Generation: tier + obligations + explanation + citations
        │  → applicability_status per determination: applicable / not_applicable /
        │    unresolved / not_in_force (§14)
        ▼
Output to user
```

Two determination-adjacent LLM steps exist (extraction, generation). Between them sits
retrieval, rule evaluation, and **exactly one constrained LLM step: the provision
matcher (§4.2)**. No other free LLM reasoning happens there. This is a deliberate design
choice to keep the legally load-bearing part of the pipeline auditable.

**[Changed in Phase 3 design, 2026-10-03]** This paragraph originally said that
everything between extraction and generation was retrieval and rule evaluation. That
left a gap. Retrieval returns candidate Annex III / Article 5 / Article 6 provisions,
but nothing specified how a free-text case is judged to *fall under* one of them.
Similarity is not membership, and the rule layer only tests `actor_role` ×
`system_category` plus dates (§12). The alternatives considered were a retrieval-score
cutoff and a fixed keyword or category mapping. Both are recorded in §20, and the score
cutoff is kept as a measured baseline arm (§18). The matcher does not weaken the
auditability goal. It sees only a closed candidate set, emits only schema-constrained
per-candidate verdicts with verbatim quotes, and every verdict is checked by code before
it is used (§4.2).

---

## 3. Extraction Schema and the Fact / Conclusion Boundary

### 3.1 Principle

The extraction stage may only capture **facts stated or reasonably implied by the
user's description**. It must never output a legal conclusion — including risk tier,
actor role, or whether a change constitutes a "substantial modification." Any field
whose value requires interpreting a legally defined term belongs downstream, in a
`GroundedDetermination` stage that retrieves the relevant provision before concluding.

This boundary was tested against a concrete edge case: `substantial_modification`
looks at first glance like a fact ("the user changed the model weights and rebranded
the UI") but is actually a legally defined trigger under Article 25. The resolution:
extract the underlying factual signals (what was changed), and treat "does this
qualify as a substantial modification" as its own grounded determination.

### 3.2 Schema (V1)

```yaml
Case:
  # Retrieval-core fields
  intended_purpose: str            # domain, e.g. "recruitment"
  ai_functionality: str            # capability, e.g. "CV screening / ranking"
  relevant_date: date              # date against which applicability/text validity
                                    # is evaluated — NOT necessarily deployment_date

  # Retrieval/classification support
  role_in_decision_making: str     # e.g. "supports hiring decision"

  # Actor-fact fields (raw facts feeding role determination downstream)
  develops_system: bool | null
  uses_system: bool | null
  puts_own_name_or_trademark: bool | null
  modifies_intended_purpose: bool | null
  modifies_safety_relevant_components: bool | null   # feeds substantial-modification
                                                      # determination, is NOT itself
                                                      # that determination

  # Context / future filtering
  jurisdiction: str | null         # not a V1 retrieval filter (single-jurisdiction Act);
                                    # retained for scope validation, eval, and future
                                    # multi-jurisdiction support

  # Grounding / explanation support (secondary retrieval discriminator)
  affected_persons: str | null

  # Explicitly excluded from extraction — downstream conclusions only:
  # risk_tier, prohibited_practice, actor_roles, applicable_articles, obligations,
  # substantial_modification (bool)
```

`relevant_date` semantics: **the date against which legal applicability and/or textual
validity should be evaluated for the user's scenario.** V1 uses a single field; splitting
into `event_date` / `assessment_date` / `deployment_date` is deferred until a concrete
use case demonstrates the single field is insufficient.

**[Changed in Phase 2, 2026-10-02]** Two fields became nullable in the extraction
output (`CaseFacts`, `src/eu_ai_check/extraction/schema.py`):

- `relevant_date: date | null`. Many descriptions state no date. Forcing a value
  would make the extractor decide a default, which is pipeline policy rather than a
  fact in the text. Extraction reports `null`; the pipeline's `Case` then substitutes
  the assessment date **explicitly** and records `relevant_date_basis: stated |
  assessment_date`, so "the user said this date" and "we defaulted it" are never
  confused.
- `role_in_decision_making: str | null`, for the same reason.

Only `intended_purpose` and `ai_functionality` remain non-nullable: every AI use-case
description has some purpose and capability, even if vague. The schema is closed
(`additionalProperties: false`; pydantic `extra="forbid"`), so the excluded fields
cannot be emitted at all. Structured outputs constrain decoding to it, and validation
rejects anything else.

### 3.3 Why the raw text is preserved separately

The extraction schema is not the retrieval query. The original free text remains
available to semantic/BM25 retrieval directly; structured fields provide query
enrichment and metadata/temporal filtering, not a replacement for the text. Structured
fields are necessarily lossier than the source description.

### 3.4 Field rules and labelling decisions (Phase 2)

Several actor and date fields are ambiguous without explicit rules. Does a fine-tuned
purchased model count as developed? Is "since January" a relevant date? If the rules
lived only in the labeller's head, the eval would measure whether the model can guess
them. The rules are therefore written once and used in two places:

- **in the extractor's system prompt** (`FIELD_RULES`,
  `src/eu_ai_check/extraction/extractor.py`), and
- **verbatim in the eval set** (`data/eval/extraction_cases.json` →
  `labelling_rules.field_rules_verbatim`). A test fails if the two drift apart.

Labelling decisions, each tied in the eval file to the cases it decides, with tests
checking the labels against them:

| # | Decision | Cases |
|---|---|---|
| L1 | Fine-tuning or retraining a system obtained from someone else is a *modification*, not development. | E11, E26 |
| L2 | A separate legal entity in the same group (e.g. a sister company) is "someone else": its system was obtained, not developed. | E25 |
| L3 | `puts_own_name_or_trademark = false` only for use under another party's name, including a **named** third-party product used without rebranding. Unnamed product or undescribed branding → `null`. Selling under one's own brand → `true`. | false: E01, E08, E13, E15, E28, E30 · null: E18 · true: E03, E10, E14, E17 |
| L4 | `uses_system` covers current **and planned** use. Build/sell/supply-only → `false`. | planned: E05, E21, E23 · supplier only: E03, E10, E14, E17 |
| L5 | Development may be inferred when the organisation calls the system its own product that it makes or sells. | E03, E14 |
| L6 | Repurposing a general-purpose system for a specific use → `modifies_intended_purpose = true`. | E11 |
| L7 | Dates: explicit launch/start date or "where we stood on X" → that date; "as of today" → the assessment date; "next month" → 1st of that month; already in use with no date asked for, or vague timing → `null`. | E17, E19, E20, E18, E13, E29, E21 |
| L8 | Null discipline: `role_in_decision_making` and `affected_persons` are `null` unless described; they are not filled in from what such systems usually do. | 15 cases, listed in the eval file |
| L9 | A legal conclusion asserted without supporting facts ("we are the provider") yields no actor facts. | E24, E27 |

### 3.5 The LLM evaluation stack: OpenAI only (Phase 2)

The whole LLM evaluation workflow runs on OpenAI and needs only `OPENAI_API_KEY`.
It has two roles, kept deliberately separate:

| Role | Model | Setting | Status |
|---|---|---|---|
| **Extractor**, the system under test | GPT-6.1 Sol, `gpt-6.1-sol` | `reasoning.effort` = `low` / `medium` (the two arms) | required |
| **Judge**, a secondary signal | GPT-6 Astra, `gpt-6-astra` | `reasoning.effort` = `low` | optional (`--no-judge`) |

**Accepted configuration (Phase 2 closure, 2026-10-02):** extractor `gpt-6.1-sol`,
`baseline` prompt, `reasoning.effort = low`. The judge is evaluation-only and is not
part of the production extraction path. See §18 for the acceptance result.

### 3.5.1 Extractor

**The extraction model is OpenAI GPT-6.1 Sol, `gpt-6.1-sol`**, called through the
Responses API (`src/eu_ai_check/extraction/openai_extractor.py`; the default in
`providers.py`).

- **Reasoning effort.** The pre-registered arms `low` and `medium` map one-to-one
  onto GPT-6.1 Sol's `reasoning.effort` values `"low"` and `"medium"`. The model
  supports `low`, `medium` (its default), `high`, `xhigh` and `max`, but not `none`
  or `minimal` (OpenAI model page, checked 2026-10-02). No approximation is
  involved, and the extractor rejects any other value.
- **Structured Outputs.** `text.format = {type: "json_schema", name: "case_facts",
  strict: true, schema: openai_strict_schema()}`. That schema is derived
  mechanically from the same `CaseFacts` pydantic model, so property set,
  `required`, `additionalProperties: false`, base types, nullability and
  descriptions are identical, and a test compares the two field by field. Only two
  things differ:
  - nullable fields use the type-array form OpenAI documents
    (`["string", "null"]`) rather than `anyOf`;
  - `"format": "date"` is not sent, because OpenAI's documentation did not confirm
    strict-mode support for string formats. The date constraint is still enforced
    by pydantic validation of every output, which fails closed on a non-ISO date.
- **Same prompt, same rules.** The system prompt, FIELD RULES and user message are
  byte-identical to the pre-registered commit.
- **Recording.** Every result records the provider, the requested model, the
  reasoning effort and the model the API reports as having served the request.
  The harness writes all of them per call to `<out>.runs.jsonl`.
- **Fail-closed.** A refusal content item, an incomplete response, invalid JSON or a
  schema mismatch gives `facts = None` with the reason. There is no server-side
  fallback on this path.
### 3.5.2 Judge

**The secondary judge is OpenAI GPT-6 Astra, `gpt-6-astra`**, at `reasoning.effort:
"low"`, through the Responses API with strict Structured Outputs
(`text.format = {type: "json_schema", name: "judge_verdicts", strict: true}`).

- **Why GPT-6 Astra.** It's OpenAI's most capable model, and it is a *different*
  model from the extractor, so no output is graded by the model that produced it.
  `low` effort matches the effort the pre-registered judge used.
- **Method unchanged.** The judge's system prompt and verdict schema are identical
  to the pre-registration (`fe0b0f6`); tests pin their SHA-256. Only the provider and
  model changed.
- **It never defines or changes the ground truth.** The judge sees the label's
  reference sentence and the extracted value, read-only, and only for text fields
  with a non-null label and a non-null prediction. Its verdict is stored next to the
  primary concept-group score and reported as agreement / disagreement. It never
  alters per-field correctness, any accuracy figure or any label. A test runs a
  judge that disagrees with everything and checks that correctness and accuracy
  are identical to a run without a judge.
- **Optional.** `--no-judge` runs the primary evaluation with the extractor alone. A
  judge refusal or malformed verdict is recorded as a judge error and yields no
  verdicts, without touching the primary score. API errors are not swallowed.

### 3.5.3 Provenance

Both changes were made on 2026-10-02: *after* the eval set was pre-registered
(`fe0b0f6`) and *before* any API call.

1. **Extractor:** Claude Opus 5.5 → GPT-6.1 Sol (commit `7434d4d`).
2. **Judge:** Claude Opus 5.5 → GPT-6 Astra, making the stack OpenAI-only.

Unchanged by either: eval set, labels, labelling rules, null discipline, leakage
check, scoring, categories, 3 repeats, effort arms, extractor prompt and judge
prompt. Tests pin SHA-256 fingerprints of the pre-registered eval content and judge
method. The models under test changed; the pre-registered evaluation did not, and no
result had been seen. The Claude Opus 5.5 extractor remains available as an optional,
non-evaluated `extract --provider anthropic`, behind the `anthropic` package extra.
Nothing in the evaluation workflow imports it.

**Review record.** The user reviewed all 30 labels before pre-registration. One
label changed during that review, E18 `puts_own_name_or_trademark` false → null
(L3: the payroll provider's tool is not named). Because the change was made before
any API run, it is *not* post-hoc. Any change after the first run is.

---

## 4. The GroundedDetermination Pattern

A single reusable stage shape, used for every legal conclusion in the system:

```
facts → retrieve relevant provisions → determine conclusion → cite evidence
```

Instantiated for:
1. Risk-tier classification (Annex III category matching)
2. Prohibited-practice determination (Article 5 matching, via the `ApplicabilityRule`
   matching engine where applicable-date conditions apply, §12)
3. Actor-role determination (including Article 25 role transfer)
4. Substantial-modification determination
5. Obligation applicability (via the `ApplicabilityRule` matching engine, §12)

This was recognized during design, not assumed upfront: risk-tier classification,
actor-role determination, and prohibited-practice determination turned out to share
identical structure (retrieve → determine → cite), so the system treats them as one
parameterized `GroundedDetermination` component (facts, retrieval scope, output
schema) rather than bespoke logic per determination type.

**`ApplicabilityRule` is not a mandatory layer for every instantiation.** It backs only
the determination types whose legal question actually requires evaluating date-scoped
or conditional applicability — obligation applicability (Article 16/26) and
prohibited-practice determination (Article 5), since both involve provisions that
become legally binding on specific dates (§12–§13). Risk-tier and actor-role
determinations conclude directly from retrieved provision content matched against case
facts, without a rule-matching layer, since Annex III category membership and role
definitions are not phased in over time the way obligations and prohibitions are.

**How "matched against case facts" happens differs by determination [Phase 3 design,
2026-10-03]:**

| Determination | Conclusion mechanism |
|---|---|
| Risk tier | Provision matcher (§4.2) over Annex III, Article 6(3) and Article 6(1) / Annex I candidates, combined by fixed code (§9.1) |
| Prohibited practice | Provision matcher over Article 5(1) candidates, then `ApplicabilityRule` dates |
| Actor role | Rules over the boolean actor facts (§3.2) and the Article 3 / Article 25 definitions; no LLM |
| Substantial modification | Rules over `modifies_*` facts; no LLM |
| Obligation applicability | `ApplicabilityRule` engine (§12); no LLM |

The matcher is used only where the legal question is "does this free-text use case fall
under this listed category?" There, both sides are open-ended natural language and no
boolean fact can stand in without becoming a legal conclusion (§3.1). Everywhere else
the inputs are already structured, so rules suffice.

### 4.1 Actor-Role Determination Output

Actor roles can be held simultaneously (an organization can be both provider and
deployer of the same system) and can change through Article 25 role transfer. The
output is a **list**, not a single enum:

```yaml
ActorRoleResult:
  actor_roles: [provider, deployer]   # zero or more; provider/deployer only in V1
  actor_role_status: known | unresolved
```

**`actor_roles: []` with `actor_role_status: known`** means the determination
positively concluded the actor holds none of the covered roles for this system — an
evidence-backed "none," not a lack of information.

**`actor_role_status: unresolved`** means the case facts were insufficient to determine
role(s) at all (analogous to §14's `unresolved` for obligations — a genuine evidence
gap, not a conclusion). `actor_roles` is meaningless/empty in this state and must not be
read as "no role."

These two empty-looking cases are never conflated: one is a determination that
concluded "no role applies," the other is a determination that could not be reached.

**[Amended in Phase 3 design, 2026-10-03] Partial knowledge.** The two-state status
could not express "certainly a deployer, provider unknown". Calling that `known` would
overstate it, and calling it `unresolved` would discard an established role. Each role
is therefore determined separately, as true, false or unknown (§4.3):

```yaml
ActorRoleResult:
  actor_roles: [provider, deployer]      # roles established true
  unresolved_roles: [provider, deployer] # roles that could not be determined
  actor_role_status: known | unresolved  # known iff unresolved_roles is empty
```

`actor_roles` is now meaningful in both states: it always lists the roles that were
established. `actor_roles: []` with `known` keeps its meaning, an evidence-backed
"none". The rule engine reads each role's own value when it evaluates an
`actor_role` condition (§14.2). An unknown role is unknown, not false.

**Downstream obligation handling for simultaneous roles:** when `actor_roles` contains
more than one role, obligation applicability (§12, §14) is evaluated **independently per
role** against the `ApplicabilityRule` engine, then combined:

```
Actor facts → actor_roles (list) → for each role:
    retrieve obligations for that role → resolve applicability/conditions
→ deduplicate (same logical_provision_id triggered by multiple roles is
  reported once, not repeated) → final obligation set
```

A provider+deployer case therefore receives the union of both obligation sets, each
independently evidence-backed, not a blind merge — a provision could be `applicable`
under one role and `not_applicable` or `unresolved` under the other, and both outcomes
are preserved per-role in `supporting_evidence` rather than collapsed to one status.

### 4.2 The Provision Matcher (Phase 3 design, 2026-10-03)

The one LLM step between extraction and generation (§2). It answers a single question
per candidate provision: does the described use case fall under this provision?

**Inputs.**
- The **original free-text description**, not just the extracted `Case`. Extraction is
  deliberately lossy (§3.3). Some matching criteria, for example the Article 6(3)
  conditions, are deliberately absent from the schema (§9.1), so the matcher must read
  the source text.
- The extracted `Case`, as context.
- A **closed candidate set**: every date-eligible provision in the determination's
  scope, each with its id and canonical text.

**The candidate set is exhaustive over the scope, not top-k.** The scopes are short
closed lists: 25 Annex III provisions, 8 Article 5(1) points, the Article 6(3)
provisions, and Annex I. Taking only the top-k would let a retrieval miss turn into a
confident `not_high_risk` or "not prohibited", which is the most dangerous error this
system can make. The Phase 1 retriever still provides the temporal pre-filter and the
candidate ordering, and it alone drives the baseline arm (below). If a scope ever grows
too large to evaluate exhaustively, top-k needs a measured recall guarantee first. That
case is not V1.

**Output (schema-constrained, one verdict per candidate):**

```yaml
MatchVerdict:
  logical_provision_id: str      # must be in the candidate set
  verdict: matches | does_not_match | insufficient
  case_quote: str | null         # verbatim span of the description; required for
                                 # matches and does_not_match
  provision_quote: str | null    # verbatim span of the candidate's canonical text;
                                 # required for matches
  rationale: str                 # for the explanation only; never read by code
```

**Code verification before any verdict is used.** Each check that fails *downgrades*
that verdict to `insufficient` and is logged. No check ever upgrades a verdict.
1. `logical_provision_id` is in the candidate set. An unknown id is discarded.
2. Every candidate has exactly one verdict. A missing verdict becomes `insufficient`.
   For duplicates, the most conservative verdict wins.
3. `case_quote` occurs verbatim in the description and `provision_quote` occurs
   verbatim in the candidate's canonical text. Both checks run after whitespace and
   quote-character normalisation only.
4. Required quotes are present for the verdict given.

**Verdict semantics.** `does_not_match` must be backed by a case quote that shows the
use case is something else, such as a spam filter quoted against Annex III 4(a).
"Nothing in the text says so" is `insufficient`, not `does_not_match`. This is what
lets `unresolved` arise from the matcher at all, instead of an absence of evidence
being silently read as a negative.

**Combination is code, not LLM.** The matcher judges candidates one at a time. How
verdicts combine into a determination status is fixed in code: §9.1 for risk tier, and
for prohibited practice:
- any Article 5(1) point `matches` → that point's `ApplicabilityRule`s decide the
  status (§12, §14);
- every point `does_not_match` → `not_applicable`;
- otherwise → `unresolved`.

Article 5(1)(h) carries its exceptions inside one canonical provision (§7.1), so a
`matches` on it means the use falls under the prohibition and none of the exceptions
apply.

**Model.** Same stack as extraction (§3.5): OpenAI, strict Structured Outputs. The
model and effort arms are fixed in the Phase 3 pre-registration, before any
determination run.

**Baseline arm: retrieval-score cutoff.** The highest-scoring candidate in a scope
counts as `matches` if its retrieval score clears a threshold τ. Every other candidate
counts as `does_not_match`. τ is fixed before the first determination run, using the
Phase 1 retrieval eval set, not the determination eval set. By construction this arm
can never produce `insufficient`, so it cannot produce `unresolved` from matching. That
limit is one of the things the comparison is meant to show (§18, §20).

### 4.3 Actor-Role Rules (Phase 3 design, 2026-10-03)

The spec named "rules over the boolean actor facts and Article 3 / Article 25" (§4) but
never wrote them down, and the eval labels depend on them. They are three-valued
(true / false / unknown), using the same logic as §14.2:

```
high_risk  = true if a risk-tier route is established (§9.1), whether or not it
             applies yet; unknown if risk tier is unresolved; else false
deployer   = uses_system                                        # Article 3(4)
substantial_modification = modifies_safety_relevant_components
                           OR modifies_intended_purpose         # Article 3(23)
provider   = develops_system                                    # Article 3(3)
             OR ( high_risk AND ( puts_own_name_or_trademark    # Article 25(1)(a)
                                  OR substantial_modification   # Article 25(1)(b)
                                  OR modifies_intended_purpose )) # Article 25(1)(c)
```

- **Evidence.** Each established or excluded role cites the provisions its rule used.
  To make that possible, Article 3 points (3), (4) and (23) are ingested. Only those
  three points are ingested, explicitly: the other 65 definitions would be retrieval
  distractors (`V1_PARTIAL_ARTICLES`).
- **Substantial modification** is the nested fifth instantiation (§4). It is a rule,
  not a matcher call. Article 3(23) defines it as a change that affects compliance or
  modifies the intended purpose, and those are exactly the two `modifies_*` facts.
- **Transfer only on high-risk systems.** Article 25(1) speaks only of high-risk
  systems. A reseller who rebrands a spam filter therefore stays roleless:
  `actor_roles: []`, `known`.
- **Known V1 simplifications, recorded rather than hidden:**
  - `develops_system` counts as provider even without the "places on the market or
    puts into service under its own name" part of Article 3(3). A contract developer
    building to a client's order is the case this gets wrong.
  - Article 25(2) (the original provider ceasing to be the provider after a transfer)
    and Article 25(3) (product manufacturers) are not modelled as role rules. Their
    provisions remain citable.

---

## 5. Canonical Legal Provision Model

**Canonical unit = the smallest legally meaningful provision that can be independently
retrieved and cited without losing the conditions required to interpret it.**

This is deliberately *not* defined as "one provision per grammatical clause" or "one
provision per fixed token count." It is defined as one provision per *independently
interpretable legal unit*, per the source's own hierarchy.

```yaml
Provision:
  logical_provision_id: str
  versioned_provision_id: str
  content_id: str
  provision_type: enum[definition, obligation, prohibition, exception, scope, annex_category, ...]
  structural_location: StructuralPath   # see §6
  text_valid_from: date
  text_valid_to: date | null
  applicability_rules: [ApplicabilityRule]   # see §10
  cross_references: [logical_provision_id]   # see §8
```

### 5.1 No first-class `Obligation` entity in V1

**[Decision, deferred pending evidence]** V1 does not introduce a separate `Obligation`
entity distinct from `Provision`. Obligation-type provisions carry `provision_type =
obligation` and hold their own `applicability_rules` directly. Prohibition-type
provisions (`provision_type = prohibition`, e.g. Article 5) follow the same pattern,
carrying their own `applicability_rules` directly where applicable-date conditions
apply (e.g. the Article 5 prohibitions' February 2025 application date) — no separate
entity is introduced for prohibitions either. This was tested against
Article 16 **[VERIFIED]**, where each lettered point (a)–(l) is already an independent,
atomic obligation under the existing chunking rule — no additional entity needed. It was
also tested against a hypothetical bundled sentence ("Providers shall ensure X and,
where Y, shall additionally ensure Z") and against the real Article 5(1)(h) case
(§7.1) — in both, the canonical-provision boundary was judged sufficient, provided
ingestion preserves enough internal structure that applicability can still be scoped
correctly within one provision.

**Escape hatch, explicit:** if corpus review during ingestion shows a meaningful number
of provisions containing multiple independently applicable obligations that cannot be
represented cleanly at the provision level, introduce `obligation_id` as a child of
`Provision`. This is a corpus-evidence-driven data-model evolution, not a speculative
V1 feature.

---

## 6. Structural Path — Variable-Depth, Not Fixed Fields

**[Decision, updated post-corpus-check]** Original design assumed a fixed field set
(`chapter/section/article/paragraph/point`). Corpus inspection **[VERIFIED]** falsified
the fixed-depth assumption:

- Article 16: `Article → Point` (2 levels; no paragraph layer used)
- Article 26: `Article → Paragraph` (2 levels; no lettered points observed)
- Article 5: `Article → Paragraph → Point → Sub-point` (4 levels, e.g. Article
  5(1)(h)(iii))

`structural_location` is therefore modeled as an **ordered path of variable length**,
not fixed named fields:

```yaml
StructuralPath:
  - type: chapter | section | article | paragraph | subparagraph | point | sub_point | annex | annex_area | annex_item
    value: str
  # ordered list, e.g.:
  # [ {article, "5"}, {paragraph, "1"}, {point, "h"}, {sub_point, "iii"} ]
```

Citation rendering and the "full structural path" display (e.g. `EU AI Act → Chapter III
→ Section 3 → Article 26 → Paragraph 2`) are derived by walking this path, not by
reading fixed columns.

**[Phase 3, 2026-10-03]** The variable-depth path absorbed two new shapes without any
model change, which is the point of this decision:
- `Article → Paragraph → Subparagraph`, for trailing subparagraphs that name no point
  (Article 6(3), third subparagraph; §9.1). It is rendered as "Article 6(3), third
  subparagraph".
- `Annex → Section → Annex item`, for Annex I, Sections A and B. It is rendered as
  "Annex I, Section A, point 11". As with chapters and sections in articles, the
  section is navigational context and is left out of `logical_provision_id`
  (`aiact:anx_I:item_11`). Annex I numbers its entries continuously across sections.

---

## 7. Chunking and Parent-Document (Small-to-Big) Retrieval

### 7.1 Why parent resolution is mandatory, not optional

**[VERIFIED against Article 5(1)(h)]** The nested-exception case is real, not
hypothetical: Article 5(1)(h) states a prohibition, carves out three numbered
exceptions (i)–(iii), and closes with a savings clause applying to the whole point
("Point (h)... is without prejudice to Article 9 of Regulation (EU) 2016/679"). An
embedding match on sub-point (iii) alone, without its parent point (h), would let the
system describe an exception without ever stating the prohibition it excepts from — the
exact failure mode the parent-resolution invariant exists to prevent.

### 7.2 Design

- **Retrieval unit (technical):** may be a smaller embedding sub-chunk when a canonical
  provision is too large for effective embedding.
- **Canonical unit (legal/citation):** the atomic legal provision per §5.
- **Invariant:** every retrieved evidence item passed to generation must resolve to its
  complete canonical legal provision — never an isolated sub-chunk fragment.

```
Query → Semantic + BM25 search → RRF fusion → Exact-citation priority
      → Matched sub-chunk(s) → Parent resolution → Full canonical provision(s)
      → Generation context
```

**[IMPLEMENTED in Phase 1]** Sub-chunking rule: a provision whose canonical text
fits in 800 characters (~200 tokens) is one chunk; a longer one is split at legal
boundaries only — sentence ends and enumerator starts such as "(ii)" — and packed
greedily up to the budget. A sentence longer than the budget is kept whole rather
than cut. Chunks rejoin losslessly to the canonical text (tested). The bound is set
by the embedding model: Article 26(10), the longest provision at 2,726 characters,
overruns bge-base's 512-token window and would otherwise lose its closing sentences
— including the deployer's annual-reporting duty — to silent truncation.

Each chunk is indexed with a contextual header, `"<citation> - <heading>"` (e.g.
`Article 26(7) - Obligations of deployers of high-risk AI systems`), because a
provision's body text never names itself. Both the semantic and BM25 sides see the
same header, so the vector-only vs hybrid comparison stays fair.

**[VERIFIED against Article 5(1)(h)]** The split is not hypothetical: one chunk of
5(1)(h) holds exception (iii) and the savings clause without the prohibition
sentence. A match on that chunk resolves to the full provision, prohibition
included — tested by name.

### 7.3 Context expansion is conservative, not automatic

Siblings (e.g. adjacent points within the same article) are **not** included by
default. Cross-referenced provisions (§8) are pulled in only when the matched
provision's own text contains an explicit structural dependency on them. This avoids
flooding generation context with adjacent-but-irrelevant provisions.

```
1. Match sub-chunk
2. Return full parent provision
3. If provision contains explicit cross-reference / structural dependency:
4.   Retrieve linked provision
   (siblings not retrieved by default)
```

### 7.4 Fusion, exact-citation priority, and reranking (Phase 1 decisions)

**Fusion: reciprocal rank fusion (RRF, k = 60).** BM25 scores are unbounded and
corpus-dependent; cosine similarities sit in a narrow band. Adding them needs a
calibration that drifts with every corpus change. RRF uses ranks only. The
constant is the published default (Cormack et al., 2009), not tuned.

**Exact-citation priority. [ADOPTED on measured evidence, 2026-10-02]** When a
query names a provision *below* article/annex level — "Article 26(7)", "Article
5(1)(h)(iii)", "Annex III point 4(a)" — the date-eligible chunks whose own
structural citation matches are moved ahead of the fused ranking. Rules:

- Article- or annex-level references ("Article 6", "Annex III") do not trigger it;
  they name dozens of provisions.
- Of nested query citations, only the deepest counts.
- A citation matches its provision, any descendant ("Article 25(3)" → 25(3)(a),
  25(3)(b)), and its containing canonical provision when it names a unit inside
  one ("Article 5(1)(h)(iii)" → 5(1)(h), since (iii) is not its own provision).
- It is a lookup among *eligible* versions only: the §13.2 hard filter still
  applies, so a citation cannot surface a version not in force on
  `relevant_date`.
- It matches the provision's *own identity*, not mentions of it in other text.

Why it exists: plain RRF dilutes an exact match. On bare-citation queries BM25
alone reached Recall@5 0.962, but fusing it with a vector ranking that cannot read
"26(7)" dropped hybrid to 0.865. BM25 also has the opposite failure — all 25 Annex
III items contain the words "pursuant to Article 6(2)", so BM25 cannot tell a
provision *citing* 6(2) from the provision that *is* 6(2). Citation priority fixed
both: Recall@5 1.000 on the bare-citation set, and no change at all on queries that
cite nothing (identical numbers on the pre-registered set; tested).

It was introduced as a separate experimental arm, with its matching rule fixed
before its first run, rather than tuned against the result. See §18 for the
post-hoc status of the queries it was measured on.

**Known limit, deliberately not fixed:** citation styles the tokenizer was not
written for — "Art. 26(6)", "Annex III, point 4(a)", "point (h) of Article 5(1)" —
get no citation priority (Recall@5 0.857 on that set vs 1.000 for bare citations).
The tokenizer was not changed to fit queries already seen. A broader citation
grammar is a candidate improvement, to be judged on queries written before it.

**Reranker: off by default, kept as an eval arm.** A cross-encoder
(bge-reranker-base) over the fused head lowered hybrid Recall@5 on use-case queries
from 0.946 to 0.875 and on bare citations from 0.865 to 0.769. A larger reranker is
not adopted speculatively; it would be evaluated as an arm first.
---

## 8. Cross-Reference Representation and Resolution

Cross-references are preserved as **graph edges in metadata**, not expanded into the
referencing chunk's text.

```yaml
cross_references:
  - Article 26
```

Rejected alternative: duplicating referenced text into every chunk that cites it.
Rejected because it causes content duplication, version-consistency risk (the embedded
copy can drift from the source provision after an amendment), inflated embeddings, and
ambiguous citation provenance.

Resolution happens at retrieval/grounding time: if a matched provision contains a
cross-reference and the determination stage judges the reference structurally necessary
to interpret it, the referenced provision is retrieved and combined as evidence.
**Multi-hop cross-reference traversal (resolving chains of references, not just one
hop) is a stretch goal, not a V1 requirement** (see §17).

---

## 9. Annex III Special Handling

**Annex III does not stand alone: Article 6 is what gives it legal effect.**
**[Scope extended on Phase 0 corpus evidence, 2026-10-01]** Article 6 is in the V1
corpus. Annex III is a list; Article 6(2) is the provision that makes appearing on that
list mean "high-risk", Article 6(1) is the separate product-embedded route, and Article
6(3) is the derogation under which an Annex III system is **not** high-risk where it
does not pose a significant risk of harm. A risk-tier determination built on Annex III
alone would collapse to "appears in Annex III ⇒ high_risk", which the Act does not say.

Article 6 was absent from the original V1 scope and was added because ingestion produced
evidence rather than an opinion: 27 of the 73 provisions ingested without it
cross-referenced it, in-corpus cross-reference resolution sat at 9.1%, and the
applicability conditions on Articles 16 and 26 already distinguished
`annex_iii_high_risk` from `article_6_1_high_risk` — a distinction only Article 6
defines. Article 6 is typed `scope` by default, since it classifies rather than imposing
duties, with per-paragraph overrides where its character differs (6(3) is an
`exception`; 6(4)–6(7) are `obligation`s).

Annex III itself is a taxonomy of high-risk use-case categories, not narrative prose,
and is
chunked accordingly: `Annex III → numbered area (1–8) → lettered item (a, b, c...)`,
with the lettered item as the canonical chunk where one exists.

**[VERIFIED — irregularity found]** Area 2 ("Critical infrastructure") has **no
lettered sub-items** — it is a single flat sentence covering the entire category. The
chunking rule therefore includes an explicit terminal case: **when an Annex III area has
no lettered breakdown, the area itself is the atomic provision.** This is not a
synthetic wrapper chunk; it reflects the source's own (lack of) subdivision.

Verified area structure, for reference:
1. Biometrics — (a), (b), (c)
2. Critical infrastructure — **no sub-items (terminal at area level)**
3. Education and vocational training — (a)–(d)
4. Employment, workers' management, access to self-employment — (a), (b)
5. Access to essential private/public services — (a)–(d)
6. Law enforcement — (a)–(e)
7. Migration, asylum, border control — (a)–(d)
8. Administration of justice and democratic processes — (a), (b)

### 9.1 Risk-tier determination: the two routes and the derogation (Phase 3 design, 2026-10-03)

**No `Case` schema change.** Article 6(3) and 6(1) depend on facts the Phase 2 schema
does not hold: whether the task is narrow or preparatory, whether the system profiles
people, whether it is a safety component, and whether third-party conformity assessment
applies. Two ways to supply those facts were considered:
- add fields to `Case`;
- have the matcher read the original description against these provisions (§4.2).

The second was chosen. Adding fields would reopen the closed Phase 2 contract: the 30
labelled cases, the field rules, the accuracy target, and the leakage check. Several of
the candidate fields would also sit on the fact/conclusion boundary. "Performs
profiling" is a term the Act defines by reference to the GDPR, so as an extraction
field it would be a legal conclusion (§3.1).

**Annex I joins the corpus.** Article 6(1)(a) and (b) refer to "the Union harmonisation
legislation listed in Annex I". Without Annex I, the product-embedded route depends on
a list that is absent, which is the same gap Phase 0 found for Article 6 itself.
Annex I is ingested like Annex III. It is a list, typed `scope`, with its Section A and
Section B entries as the atomic provisions. **[VERIFIED at ingestion]** It has 20
entries numbered continuously: Section A covers 1–12 (New Legislative Framework, e.g.
machinery, toys, medical devices), and Section B covers 13–20 (e.g. civil aviation,
vehicles, rail). Each entry carries its own section heading as a lead-in (§6). Its OJ
markup differs from every other V1 list: a three-column row with an empty indent cell,
and the text in a bare `<span>`. The parser handles both.

**Combination (fixed code over verified matcher verdicts):**

*Article 6(2), the Annex III route:*
- Any Annex III candidate `matches` → Annex III membership is established. Go to the
  Article 6(3) check.
- Every Annex III candidate `does_not_match` → the route is not met.
- Otherwise → the route is `unresolved`.

*Article 6(3), evaluated only after an Annex III match:*

| Profiling override (6(3), third subparagraph) | Any of 6(3)(a)–(d) | Route outcome |
|---|---|---|
| `matches` | — | high-risk (Annex III) — the override always wins |
| `does_not_match` | `matches` | **not high-risk** (derogation applies; cited) |
| `insufficient` | `matches` | `unresolved` — the override would flip the outcome |
| any | none `matches` | high-risk (Annex III), with `derogation: not_established` recorded |

The last row is a deliberate asymmetry. The derogation is an exception that the
provider must positively claim and document under Article 6(4). The Act's default for
an Annex III system is high-risk. A description that is silent on 6(3) therefore does
not establish the derogation. It does not leave the tier unresolved either. The output
records that the derogation was considered and not established, so the result is never
a bare "high-risk" that hides the question.

*Article 6(1), the product-embedded route.* Both conditions are required:
- (a) covered by Annex I legislation, judged by matching against Annex I entries;
- (b) subject to third-party conformity assessment, judged by matching against 6(1)(b).

| (a) | (b) | Route outcome |
|---|---|---|
| `matches` | `matches` | high-risk (Article 6(1)) |
| either `does_not_match` | — | route not met |
| otherwise | | `unresolved` |

Descriptions rarely state the conformity-assessment requirement. Expect this route to
be `unresolved` unless the description says so explicitly. That is accepted V1
behaviour, not a defect to tune away.

*Route dates.* An established route counts as high-risk only on or after its
`ApplicabilityRule` date: 2026-08-02 for Article 6(2) and 2027-08-02 for Article 6(1)
(§12.1). Before that date the route is **established but not yet applicable**.

*Routes combined into `risk_tier`:*
- Either route reaches high-risk and is applicable on `relevant_date` → `applicable`.
- Otherwise, any route is `unresolved` → `unresolved`.
- Otherwise, a route is established but not yet applicable → `not_applicable` with
  `not_applicable_reason: not_yet_applicable`.
- Otherwise (both routes not met, or the derogation applies) → `not_applicable` with
  `not_applicable_reason: conditions_not_met`.

The output carries `system_category`, which is `annex_iii_high_risk`,
`article_6_1_high_risk`, or both. It is set for every **established** route,
including one that is not yet applicable. Downstream obligations therefore come out
as `not_yet_applicable` rather than `conditions_not_met`. Downstream `ApplicabilityRule` conditions select on
exactly this value (§12). This field is what makes the two application dates
(2026-08-02 and 2027-08-02) reachable. Before Phase 3, the risk-tier output (§16) had
no slot for it.

**Corpus defect found while designing this section, and fixed [2026-10-03].** The
Article 6(3) profiling override was absent from every ingested provision. So was the
second subparagraph of Article 5(2). Both are trailing paragraph-level text that names
no specific point. Phase 0 attached trailing text only when it *named* a node (the
Article 5(1)(h) savings clause) and silently dropped everything else. This is a variant
of Phase 0 hazard (2) that the original fix did not cover.

The fix applies one rule:
- Trailing text that names a point stays a qualifier of that point, as before.
- Trailing text that names no point becomes its own canonical provision at a new
  `subparagraph` level (§6). It is numbered as the Act cites it: each own-text
  fragment opens a subparagraph, and a list belongs to the fragment before it. That
  gives `aiact:art_6:para_3:subpara_3`, "Article 6(3), third subparagraph", and
  `aiact:art_5:para_2:subpara_2`.
- Its text carries the paragraph's preceding standalone subparagraphs, but not list
  lead-ins. So "Notwithstanding the first subparagraph" is read together with the
  derogation it overrides.

Attaching the override to each of 6(3)(a)–(d) was rejected. The override needs its own
matcher verdict in the table above, and attaching the Article 5(2) rule to (a) and (b)
would cite a registration duty as "Article 5(2)(a)". A Phase 4 evidence-resolution
item follows from the split: citing a 6(3) point must also bring in the override, as
an explicit dependency (§8). A new test asserts that every source fragment of a V1
article or annex appears in some canonical provision. Before the fix it found exactly
these two fragments.

While Annex I was being added, a second Phase 0 defect surfaced in cross-reference
extraction. The external-instrument exclusion could be dodged by regex backtracking:
"Article 35 of Regulation (EU) 2016/679" failed the exclusion as "Article 35" but then
matched as an internal "Article 3". Five provisions carried false edges of this kind,
in Article 26(9)–(11) and Annex III 6(d)–(e). They are fixed and tested.

Corpus after both fixes and Annex I: 107 provisions (was 85) and 76 rules (unchanged).
None of the 85 existing provisions changed text or `content_id`. Cross-reference
resolution is 0.174 (was 0.120): 23 distinct edges (was 25), 4 of them resolvable.

---

## 10. Identity Model: `logical_provision_id` / `versioned_provision_id` / `content_id`

Three separate identities, each answering a different question:

```yaml
logical_provision_id:    # stable legal identity across all amendments
versioned_provision_id:  # a specific temporal/legal version of that provision
content_id:              # the canonical text actually embedded (SHA-256 of
                          # normalized text)
```

```
LogicalProvision (article_26_para_1)
  ├── Version A: versioned_provision_id=V_A, content_id=C123, text_valid=2024→2025
  └── Version B: versioned_provision_id=V_B, content_id=C123, text_valid=2025→present
```

**Why three, not two:** two versions of the same logical provision can share
byte-identical canonical text (e.g. renumbering elsewhere in the Article that doesn't
touch this paragraph's wording). Collapsing version identity and content identity would
force redundant embeddings for identical text; collapsing legal identity and version
identity would make "what does Article 26(1) say today vs. in 2024" unanswerable.

**Ingestion invariant:** `content_id = SHA-256(normalized_canonical_text)`. Normalization
is conservative — legally meaningful formatting, numbering, and punctuation are never
stripped purely to increase deduplication rate. The source text remains authoritative.

---

## 11. Text Versioning

`text_valid_from` / `text_valid_to` answer **only**: "what did the provision's text say
at date X?" This is distinct from applicability (§12) — a provision's text can be
unchanged since 2024 while the obligation it creates only becomes legally binding on a
much later date (the AI Act's staggered application schedule).

**Version integrity invariant:** for a given `logical_provision_id`, `text_valid_from` /
`text_valid_to` ranges across versions must not overlap. Gaps are permitted only when
explicitly represented as "not in force / not present," never as an ingestion accident.
This should be a validated constraint at ingestion time, not discovered at query time.

---

## 12. Applicability Rules and the Rule-Matching Engine

Applicability is modeled as a separate structured layer, not a second date pair
bolted onto the provision:

```yaml
ApplicabilityRule:
  rule_id: str
  legal_provision_id: str      # references logical_provision_id — applicability
                                # is a property of the legal rule across its
                                # lifetime, independent of textual redrafting
  applicable_from: date
  applicable_to: date | null
  conditions: dict              # e.g. system_category=annex_III_high_risk,
                                 # actor_role=provider
  scope: dict
  source_reference: str
```

This layer exists because applicability can depend on system category, actor role, and
phase-in schedule — factors that vary independently of the provision's own text history,
and that the AI Act's staggered application dates (prohibitions from Feb 2025, GPAI
obligations from Aug 2025, most high-risk obligations from Aug 2026, embedded-product
obligations from Aug 2027) make a first-class modeling concern rather than an edge case.

**[VERIFIED against Article 113 in Phase 0]** The concrete case that makes a conditioned
rule layer necessary rather than merely tidy: Article 113(c) defers "Article 6(1) and
the corresponding obligations in this Regulation" to 2027-08-02, while the general date
is 2026-08-02. The *same* Article 16 provider-obligation text therefore starts applying
a year apart depending on which high-risk route the system falls under — Article 6(2)
(Annex III) or Article 6(1) (product-embedded). One date per provision cannot express
this; a second date pair on the `Provision` would have to be duplicated per category and
would re-conflate text validity with applicability. Each high-risk obligation provision
consequently carries two rules differing only in `conditions.system_category`.

Sub-chunks and content records never carry applicability metadata directly — it lives
at the version/provision level and is resolved via the rule-matching engine. This
engine is not a mandatory layer for every `GroundedDetermination` instantiation (§4):
`Case facts → Rule matching → Applicable provisions` is reused specifically by
obligation applicability (Article 16/26) and prohibited-practice determination
(Article 5) — the two determination types whose legal question depends on date-scoped
or conditional applicability. Risk-tier and actor-role determinations do not use this
layer; they conclude directly from retrieved provision content. **[Qualified in Phase
3 design, 2026-10-03]** One exception: once a risk-tier route is established, the
determination reads that route's Article 6(1) / 6(2) rule **for its date only**
(§9.1, §14.2). It never reads the rule's conditions.

### 12.1 Ingestion and validation (Phase 0 responsibility)

`ApplicabilityRule` records are ingested and validated in Phase 0, alongside `Provision`
ingestion, so the corpus foundation is complete before retrieval and determination work
begins in later phases (PROJECT_PLAN.md §6). This is ingestion of *rule* data, distinct
from and additional to the `Provision.text_valid_from/to` ingestion in §11 — the two
remain separately modeled and separately validated, per the text-validity /
applicability distinction (§13.1).

**Ingestion-time invariants, validated at load time and not deferred to query time:**

- **Temporal-field validity:** `applicable_from` is present; `applicable_to`, if
  present, is strictly after `applicable_from`. `applicable_from`/`applicable_to`
  ranges are recorded verbatim from source material — never inferred or defaulted from
  `text_valid_from/to` (the two are independent facts, §13.1).
- **Provision-reference integrity:** every `ApplicabilityRule.legal_provision_id`
  resolves to an existing `logical_provision_id` ingested as a `Provision` (§5). A rule
  referencing a non-existent provision is an ingestion failure, not a warning.
- **Conditions/scope well-formedness:** `conditions` and `scope` are non-empty and use
  only recognized keys for V1 (e.g. `system_category`, `actor_role`) — an
  unrecognized or empty condition set on an ingested rule is flagged rather than
  silently accepted, since a malformed rule would otherwise fail closed at query time
  in a way that is hard to distinguish from a genuine coverage gap.
- **No orphan provisions requiring rules:** every `Provision` with `provision_type =
  obligation` or `provision_type = prohibition` (Article 5, 6(4)–6(7), 16, 25, 26 in V1
  scope) either has at least one associated `ApplicabilityRule` or is explicitly logged
  as an intentional gap — an obligation/prohibition provision silently ingested with
  zero rules is the source of a false `unresolved` later, and Phase 0 is where that
  should surface, not Phase 3.

  **[VERIFIED in Phase 0]** The escape hatch is load-bearing, not decorative. Article
  6(5)–(7) place duties on the Commission (issuing guidelines, adopting delegated
  acts), an actor V1 does not model. They are ingested with an explicit
  `applicability_gap_reason`, so this check reports them as declared gaps. A declared
  gap does not fail ingestion; an undeclared one does. The distinction is the point:
  it converts an invisible omission into a reviewable statement.

  A rule may also be declared for a **non**-operative provision. Article 6(1) and 6(2)
  are `scope` provisions — they classify rather than impose duties — but Article 113(c)
  dates Article 6(1) itself to 2027-08-02 while 6(2) falls under the general
  2026-08-02 date, so both carry rules. Without this, a determination cannot separate
  "this system is not high-risk" from "that classification route did not yet apply on
  the relevant date".

  **[Corrected in Phase 3 design, 2026-10-03]** This passage originally called that
  the `not_applicable` vs `not_in_force` distinction. It is not. Under §14,
  `not_in_force` means only that no text version was valid on the date. A phase-in
  date that has not arrived is a rule-layer outcome: `not_applicable` with
  `not_applicable_reason: not_yet_applicable` (§14.2). The rules on 6(1)/6(2) are
  what let that reason be given instead of `conditions_not_met`.

These checks are additive to, not a replacement for, the version-integrity invariant in
§11 — a `Provision` can pass text-validity validation while its `ApplicabilityRule`
data fails validation, and vice versa; the two invariant sets are checked
independently, matching the two independent temporal questions in §13.1.

---

## 13. Temporal Retrieval Semantics

### 13.1 Two independent temporal questions

1. **Text validity** — was this text version in force on `relevant_date`?
2. **Applicability** — was the obligation/rule legally binding on `relevant_date`,
   for this case's actor role and system category?

Text validity being true does **not** imply applicability is true (§14).

### 13.2 Filtering is a pre-retrieval hard constraint, not a ranking signal

```
Case.relevant_date
        │
        ▼
Temporal eligibility filter  (text_valid_from <= relevant_date < text_valid_to)
        │
        ▼
Eligible version-level index documents
        │
   ┌────┴────┐
   ▼         ▼
Semantic    BM25
   │         │
   └────┬────┘
        ▼
   Candidate set → Reranking
```

**Rejected alternative:** retrieve broadly across all versions, then filter by date
post-hoc. Rejected because of candidate starvation — if multiple historical versions of
a provision exist, lexically/semantically closer *current* versions can dominate top-K
before a date filter is applied, potentially eliminating the correct historical version
from the candidate set entirely. Increasing K as a workaround treats a correctness
problem as a recall-tuning problem.

**Rejected alternative:** encoding temporal proximity as an added ranking score
component. Rejected because it would let a legally invalid version outrank a valid one
— temporal validity is a hard eligibility gate, never traded off against relevance.

**[MEASURED in Phase 1 — candidate starvation]** On a fixture giving 8 provisions a
second text version (synthetic, eval-only, §18), with queries phrased in the amended
wording and dated before the amendment:

| arm | R@1 | R@5 | invalid versions in top-1 / top-5 |
|---|---|---|---|
| pre-filter (vector) | 1.000 | 1.000 | 0 / 0 |
| post-filter (vector) | **0.500** | 1.000 | 0 / 0 |
| no filter (vector) | 0.500 | 1.000 | 9 / 28 |

Post-filtering loses the correct version from the top-1 on half the queries; the
effect washes out by k = 5 only because the fixture adds one extra version per
provision — real corpora with longer amendment histories have no such ceiling. With
no filter, a query dated before entry into force returns provisions that did not yet
exist. Pre-filtering is applied to *both* retrievers: the vector side as a payload
filter, and the BM25 side by building the index over exactly the eligible chunks, so
an ineligible version cannot even shape IDF.

**[VERIFIED in Phase 1 — physical pre-filtering on Qdrant; was an ASSUMPTION]**
Logical pre-filtering does not guarantee *physical* pre-filtering. Measured on a
Qdrant 1.19.1 server (`eu-ai-check bench-filter --qdrant-url ...`;
`data/eval/results/filter_bench_qdrant_1.19.1.txt`), HNSW forced on, Recall@10 against
exact search under the same `eligibility_filter`, 20k seeded random vectors:

| filter admits | 10% | 1% | 0.1% |
|---|---|---|---|
| with date payload indexes | 0.898 | 1.000 | 0.904 |
| **without** date payload indexes | 0.224 | **0.007** (100/100 queries short) | 0.041 |

Without payload indexes the graph is traversed blind to the filter and returns
almost nothing eligible — the physical counterpart of candidate starvation. Qdrant
builds filter-aware extra graph links only for fields indexed when the graph is
built, so this is a **hard index requirement**:

> **The `valid_from` / `valid_to` payload indexes must exist before HNSW
> construction.** The vector store creates them immediately after the collection and
> before any point is inserted.

Qdrant's default query planner additionally switches to an exact payload-index scan
below its full-scan threshold (recall 1.000 at ≤10% selectivity in every
configuration), which is a second safety net, not a substitute for the indexes.

Caveats, stated rather than hidden: random 768-dimensional vectors are a structure-
free worst case (unfiltered HNSW recall on them is only ~0.33), so the absolute
numbers bound behaviour rather than predict production; the with/without-index
contrast is the finding. On the real corpus (96–104 points) Qdrant builds no graph
by default and searches exactly; with a graph forced, filtered recall was 1.000 and
embedded mode matched server exact search on 98/98 eval queries. Embedded local mode
is exact scan only — it proves the filter is logically right and nothing about HNSW.

### 13.3 Current and historical queries share one mechanism

A query for "today" and a query for "as of 2024-06-01" differ only in the value of
`relevant_date` fed into the same retrieval pipeline. No separate "current index" /
"historical index" code path. Physical storage may still use partitioning for
performance, but that is an infrastructure optimization layered under one logical
retrieval abstraction, not a second application-level concept.

---

## 14. Evidence Resolution and the Fail-Closed Applicability Join

Four states, not three. `not_in_force` (a temporal fact about the provision) and
`not_applicable` (a case-level conclusion about this specific case) were previously
conflated under one label; they answer different questions and must never be produced
interchangeably.

```
logical_provision_id
        │
        ├── Text version valid at relevant_date?
        └── ApplicabilityRule(s) covering relevant_date?
                    │
                    ▼
   NO_TEXT_VALID_AT_RELEVANT_DATE
        → not_in_force
          (the provision itself had no valid text in force on this date —
           says nothing about this case)

   TEXT_VALID + no ApplicabilityRule found covering the case/date at all
        → unresolved
          (rule coverage gap — NOT dropped, NOT inferred applicable,
           NOT inferred not_applicable)

   TEXT_VALID + ApplicabilityRule found, conditions evaluated, case does NOT
   satisfy them (e.g. wrong actor_role, wrong system_category, date before
   the rule's applicable_from)
        → not_applicable
          (an explicit, evidence-backed exclusion — the rule was found and
           it says this case is out of scope)

   TEXT_VALID + ApplicabilityRule found, conditions satisfied
        → applicable
```

**Invariant: textual validity is necessary but not sufficient for legal applicability.**
A provision existing and being textually current does not mean its obligation binds
anyone yet (e.g. a high-risk obligation whose text exists today but whose phase-in date
has not yet arrived for this system category) — that case is `unresolved` or
`not_applicable` depending on whether a rule was found and evaluated, never conflated
with `not_in_force`.

**Invariant: `not_in_force` must never be represented as `not_applicable`.** "No text
was in force at this date" is a fact about the provision's own history, independent of
the case. "Does not apply to this case" is a case-level conclusion reached only after a
matching rule was actually found and evaluated. Collapsing the two would let the system
report a confident-sounding case-level exclusion when the real reason is that the
provision didn't exist yet — a materially different and more dangerous claim.

`unresolved` and `not_applicable` are both surfaced explicitly rather than collapsed
into each other or into `applicable`: `unresolved` marks a genuine gap in rule coverage
that requires more evidence or human review; `not_applicable` is a positive,
evidence-backed conclusion. This four-way outcome is a first-class part of the output
contract, not an internal-only debugging state — see §16.

### 14.1 The four-state contract applies uniformly; the mechanism differs by determination type

The join described above (`TEXT_VALID` × `ApplicabilityRule` coverage) is how obligation
applicability and prohibited-practice determination reach their status, since both use
the `ApplicabilityRule` engine (§4, §12). Risk-tier and actor-role determinations do not
use `ApplicabilityRule` and reach the same four states through the mechanism proper to a
plain `GroundedDetermination` instantiation:

- `not_in_force` — the matched provision's own text was not valid at `relevant_date`
  (same meaning as above, evaluated against text validity only).
- `unresolved` — retrieved evidence is insufficient to reach a conclusion (an evidence
  gap, not a rule-coverage gap, since there is no rule set to have gaps in). For
  matcher-backed determinations this means the combination in §4.2 / §9.1 hit an
  `insufficient` verdict that could change the outcome. A verdict downgraded by code
  verification counts the same way.
- `applicable` / `not_applicable` — a direct conclusion drawn from matching retrieved
  provision content against case facts.

The four-way output contract (§16) is uniform across all determination types; only the
mechanism producing it differs depending on whether the determination is
`ApplicabilityRule`-backed.

### 14.2 Phase-in dates and unknown inputs (Phase 3 design, 2026-10-03)

Two gaps in the join above were found in the Phase 3 audit and settled before any
determination code was written.

**A date before a rule's `applicable_from` gives `not_applicable`, with a reason.**
The join already routed this case to `not_applicable`. One sentence in §12.1 framed it
as a `not_in_force` question, and has been corrected. `not_in_force` stays reserved for
"no text version was valid on this date". But a bare `not_applicable` would read as a
permanent exclusion for an obligation that starts next year. Every `not_applicable`
therefore carries a reason (§16):

- `conditions_not_met` — a rule covering the date was found, and the case fails its
  conditions (wrong role, wrong category);
- `not_yet_applicable` — the case satisfies a rule's conditions, but `relevant_date`
  is before that rule's `applicable_from`.

When both could apply, `not_yet_applicable` wins. It is the more informative and less
reassuring of the two.

"A rule was found" in the §14 join means that at least one `ApplicabilityRule` exists
for the provision. Its date window is then evaluated like a condition. Only a provision
with **no** rule at all is the coverage gap that gives `unresolved`. Otherwise every
date before the first `applicable_from` would read as a coverage gap, and the phase-in
schedule could never produce an evidence-backed answer.

The PLAN §8 acceptance case runs on the real corpus with Article 16(a) for an Annex III
provider:

| `relevant_date` | Status | Reason |
|---|---|---|
| 2024-07-01 | `not_in_force` | text not yet in force; entry into force was 2024-08-01 |
| 2025-06-01 | `not_applicable` / `not_yet_applicable` | rule found; it applies from 2026-08-02 |
| 2026-09-01 | `applicable` | rule applies |

Verified against the ingested rules. `aiact:art_16:point_a#annex3` applies from
2026-08-02 for a provider and `annex_iii_high_risk`. The `#art6_1` rule fails on
category, so it is not met at any date for this case.

**Unknown condition inputs use three-valued logic.** Rule conditions do not read raw
facts. They read `actor_role` and `system_category`, which are themselves the outputs
of earlier determinations. A condition is therefore *unknown* exactly when its upstream
determination is `unresolved`: `actor_role_status: unresolved`, or a risk tier that is
`unresolved` and leaves `system_category` undetermined. `relevant_date` is never
unknown, because `Case` always fills it in (§3.2). Evaluation:

- *Within one rule, all conditions must hold:*
  - any condition is false → the rule is not met;
  - otherwise, any condition is unknown → the rule is unknown;
  - otherwise → the rule is met.
- *Across the rules for one provision, one met rule is enough:*
  - any rule is met → `applicable`;
  - otherwise, any rule is unknown → `unresolved`;
  - otherwise → `not_applicable`, with the reason as above.

A known deployer whose risk tier is unresolved is therefore `not_applicable` for a
provider-only rule, because the role condition already fails. That is an
evidence-backed exclusion, not a guess. The same deployer is `unresolved` for a
deployer rule that needs the unresolved category. Treating every unknown input as
`unresolved` was rejected: it would discard an exclusion the evidence already
supports, and inflate `unresolved` until it stopped carrying information.

---

## 15. Index Structure: Version-Level Documents, Content-Level Embedding Dedup

Two different kinds of deduplication, kept deliberately separate:

- **Embedding computation deduplication** (guaranteed V1 property): identical canonical
  text across multiple versions is embedded **once** — the embedding model is called a
  single time per unique `content_id`, and the resulting vector is reused wherever that
  `content_id` recurs. This is an application-layer guarantee, independent of any
  specific vector database.
- **Searchable-index deduplication (explicitly avoided):** the ANN index's searchable
  documents remain **one per `versioned_provision_id`**, even when several versions
  reuse the same computed embedding.

**[ASSUMPTION — do not assume physical vector storage is deduplicated]** Whether the
chosen vector database physically stores one shared vector across multiple
`IndexDocument`s, or stores a full copy of the vector per document, is an
infrastructure-level implementation detail that depends on the specific database and
must be benchmarked/verified during Phase 1 — it is not something V1 relies on for
correctness. The only guaranteed property is that the embedding is *computed* once per
`content_id`; downstream storage efficiency is a possible optimization, not an
architectural assumption.

```yaml
IndexDocument:
  versioned_provision_id: str
  logical_provision_id: str
  content_id: str
  embedding: vector          # computed once per content_id; whether storage is
                              # physically shared across IndexDocuments with the
                              # same content_id depends on the vector DB — verify,
                              # don't assume
  text_valid_from: date
  text_valid_to: date | null
  structural_location: StructuralPath
  # other filterable metadata
```

**Why index-level dedup is rejected:** the temporal pre-filter (§13.2) operates on
metadata that lives at the version level. If the index collapsed to one document per
`content_id`, there would be no version-specific `text_valid_from/to` to filter on for a
version whose text happens to match another version's text — pre-filtering would become
impossible for exactly the cases where content is unchanged across a version boundary.
Compute-time dedup and index-time dedup are answering different questions ("how many
times do we pay to compute this vector" vs. "how many legally distinct, independently
filterable occurrences exist") and must not be conflated.

---

## 16. Output Contract: Citations and Fail-Closed States

> **As-built note (2026-10-06).** Phase 3 does not emit this shape directly. §16.1
> records what it actually produces, and §16.2 the Phase 4 adapter that maps it onto
> this contract.

The evidence/citation shape is shared across **all four top-level determinations**
(risk-tier, prohibited-practice, actor-role, obligation applicability), not defined
only for obligations. Each determination's evidence items reference both identities:
`logical_provision_id` for "which legal rule," `versioned_provision_id` for "which
specific text version was actually retrieved and cited." `PROJECT_PLAN.md`'s
traceability requirement ("every claim traceable to a specific
`versioned_provision_id`") is met by carrying the version-level identity through
evidence, not just the logical one.

```yaml
EvidenceItem:
  logical_provision_id: str
  versioned_provision_id: str      # the exact text version actually cited
  provision_citation: StructuralPath rendering (e.g. "Article 26(2)")
  excerpt: str                     # canonical provision text / excerpt

Determination:            # shared shape for risk-tier, prohibited-practice, actor-role,
                           # obligation
  determination_type: risk_tier | prohibited_practice | actor_role |
                       obligation_applicability
  applicability_status: applicable | not_applicable | unresolved | not_in_force
  not_applicable_reason: conditions_not_met | not_yet_applicable | null
                           # set iff status is not_applicable (§14.2)
  explanation: str
  supporting_evidence: [EvidenceItem]
```

`Obligation` (per-obligation output) is one instantiation of `Determination` with
`determination_type = obligation_applicability`; risk-tier, prohibited-practice, and
actor-role outputs use the same shape rather than a bespoke structure each.

**Value mapping for `risk_tier` and `prohibited_practice`.** Both determination types
reuse the generic `applicability_status` field rather than a separate tier enum:

- `determination_type = risk_tier`: `applicable` means the system is high-risk under
  either Article 6 route (`high_risk`). `not_applicable` means it is not
  (`not_high_risk`): both routes are not met, or the Article 6(3) derogation applies.
  `unresolved` and `not_in_force` keep their standard §14/§14.1 meanings, applied to
  the route combination in §9.1. **[Changed in Phase 3 design, 2026-10-03]** This
  originally read "matches an Annex III high-risk category", which is the
  "Annex III ⇒ high-risk" collapse §9 warns against. A risk-tier `Determination` also
  carries:

  ```yaml
  system_category: [annex_iii_high_risk | article_6_1_high_risk]  # every established
                                                                  # route (§9.1)
  derogation: applied | not_established | overridden_by_profiling | not_assessed
                    # Art 6(3); not_assessed when no Annex III match;
                    # overridden_by_profiling when a 6(3) condition matches but the
                    # profiling override also matches (§9.1)
  ```
- `determination_type = prohibited_practice`: `applicable` means the case matches an
  Article 5 prohibited practice; `not_applicable` means it does not; `unresolved` and
  `not_in_force` retain their standard meanings, applied to Article 5 matching (via
  `ApplicabilityRule`, since Article 5 carries its own applicable-date conditions —
  §5.1, §12).

No separate `risk_tier` enum (e.g. `high_risk`/`not_high_risk`/`prohibited`) is
introduced — `high_risk` vs. `not_high_risk` is exactly the `risk_tier` determination's
`applicable`/`not_applicable` status, and prohibition is a distinct determination type,
not a third tier value. This keeps risk-tier classification and prohibition as separate
evidence-backed conclusions rather than overloading one field with two different legal
questions.

`unresolved` and `not_in_force` must both be visible in user-facing output, not just
internal logs — a compliance tool that silently omits gaps in its own rule coverage, or
silently conflates "no rule was in force" with "this doesn't apply to you," is more
dangerous than one that admits uncertainty. See §14 for the precise definition of each
state.

### 16.1 What Phase 3 actually produces (as-built, recorded 2026-10-06)

The shape above is the **contract**. Phase 3 does not emit it directly. The Phase 4
design audit (`docs/phase4_design.md` §0) compared it with the code and the cached
results, and found these differences. They are recorded here so this section describes
what exists rather than what was intended.

- **No `EvidenceItem` type exists in Phase 3.** Evidence is spread across
  `ProvisionStatus.versioned_provision_id` (obligations), `RoleResult.evidence`
  (logical ids only) and the matcher's `Verdict`s (`provision_quote`, `case_quote`,
  `rationale`).
- **One composite object, not one `Determination` per type.** `pipeline.Determination`
  holds `risk_tier: RiskTier`, `prohibited: Prohibition`, `roles: RoleResult | None`,
  `obligations: {article: [ProvisionStatus]}` and the `MatchResult`. There is no
  `determination_type` field.
- **Status and reason share one string.** `logic.py` uses
  `"not_applicable/conditions_not_met"` and `"not_applicable/not_yet_applicable"`.
  The pair maps one-to-one onto `applicability_status` + `not_applicable_reason`.
- **No `explanation` field.** The only prose is the matcher's per-candidate
  `rationale`. Rule-based outcomes have none.
- **Actor role is not four-state.** `RoleResult` holds a three-valued value per role,
  and its status is `known` / `unresolved`. `role_label()` reports `not_in_force` when
  roles were not computed.
- **The cached `*.runs.jsonl` records keep less than this.** `Run.record()` drops
  `versioned_provision_id`, excerpts and per-provision obligation statuses. It keeps
  one status per obligation article (`group_status`). For the matcher, it keeps every
  verdict with its quotes and rationale.
- **`case_quote` is user text.** It is a code-verified verbatim span of the user's
  description.

**The full in-memory object is recoverable at zero cost.** Replaying the recorded
verdicts through `Pipeline` reproduces every one of the 123 M-low records of the
official run exactly (`scripts/verify_generation_inputs.py`). Every cited
`versioned_provision_id` and excerpt follows from the corpus and `relevant_date`
alone: each of the 110 provisions has exactly one version, valid from 2024-08-01 with
no end date. The exceptions are D38#0–2 (`relevant_date` 2024-06-15). They are
`not_in_force` throughout, so by definition they have no valid version to cite (§16.2).

### 16.2 Generation input: the Phase 4 adapter (design approved 2026-10-06)

A deterministic adapter (no LLM) maps the replayed `pipeline.Determination` onto the
contract above and adds three fields. This is everything the generator ever receives
(invariant 23).

```yaml
GenerationInput:
  case_ref: str                     # e.g. "D10#0"; opaque to the model
  relevant_date: date
  determinations: [Determination]

Determination:                      # the contract above, plus:
  determination_id: str             # "risk_tier", "prohibited_practice",
                                    # "actor_role:provider", "obligation:aiact:art_26"
  unresolved_inputs: [MissingInput] # non-empty iff applicability_status is unresolved

EvidenceItem:                       # the contract above, plus:
  evidence_id: str                  # unique within one GenerationInput

MissingInput:
  kind: matcher_question | case_fact | upstream_determination
  ref: str                          # provision id, CaseFacts field, or determination_id
  question: str                     # code-owned text (scope question, field description)
  rationale: str | null             # matcher rationale, for insufficient verdicts only
```

Mapping rules:
- **Status:** the compound string is split into `applicability_status` +
  `not_applicable_reason`.
- **Actor role:** one `Determination` per role. `True` → `applicable`, `False` →
  `not_applicable` / `conditions_not_met`, unknown → `unresolved`, roles not computed
  → `not_in_force`.
- **Obligations:** one `Determination` per article (`aiact:art_16`, `aiact:art_26`),
  with the article's `group_status`. The cached data holds one status per article, and
  V1's rules are uniform within an article. The output must not suggest a finer
  granularity than that. A `mixed` group status makes the adapter raise an error
  rather than choose a status. The evidence is the article's provisions. Their
  excerpts are included only when the article is `applicable` or
  `not_yet_applicable`, and otherwise the citation alone is given.
- **Risk tier / prohibited practice:** these cite the provisions whose verdict is
  `matches`, the route provisions (Article 6(1)/6(2)), and the Article 6(3)
  provisions when assessed.
- **`unresolved_inputs`:** these list only the `insufficient` verdicts whose flip to
  true or false would change the outcome under the fixed §9.1 combination, together
  with the `CaseFacts` fields that are null and feed a role formula (§4.3) and the
  unknown rule conditions (`rule_outcomes`), each with its upstream determination.
- **Excluded from the input:** the user's description and `CaseFacts` free text,
  every `case_quote`, rationales other than those of `insufficient` verdicts in
  `unresolved_inputs`, uncited `does_not_match` verdicts, and all eval labels.
- **`not_in_force`:** a `not_in_force` determination carries no `EvidenceItem`, because
  no version was valid on the date. The renderer states the date the text entered
  into force (from the corpus) as a plain fact, not as evidence.
- **`explanation`:** built by code from structured state (route states, derogation,
  failing condition). It is never written by an LLM.

### 16.3 Generation output (design approved 2026-10-06)

**Claims first, prose rendered by code.** The model returns strict JSON:

```yaml
GeneratedAnswer:
  claims:
    - claim_id: str
      determination_id: str      # an input determination_id
      text: str
      evidence_ids: [str]        # >= 1, all from that determination's supporting_evidence
      quote: str | null          # optional verbatim span of one cited excerpt
      quote_evidence_id: str | null
```

Code renders the answer in a fixed order: risk tier, prohibited practice, roles,
obligations per article. For each determination, the **status line is written by
code** from the input status. The model's claims for that determination follow, with
citations rendered by code from the `EvidenceItem`s. For `unresolved`, code renders
"To decide this, we would need to know:" from `unresolved_inputs`, worded as missing
information, never as a system failure. `not_in_force` lines are never worded as
`not_applicable`. The schema has no status field, so a model cannot change a status.

A deterministic **template renderer** produces the same rendering with no model
claims at all. It is the Phase 4 baseline (§18), and also the fallback for any
generated answer that fails a check.

---

## 17. Retrieval Architecture Summary (Hybrid)

```
Query + relevant_date + case facts
        │
        ▼
Temporal eligibility filter (hard constraint, pre-retrieval; date payload
indexes built before HNSW, §13.2)
        │
   ┌────┴────┐
   ▼         ▼
Semantic    BM25        (bge-base-en-v1.5 / citation-aware Okapi BM25,
   │         │           both over eligible chunks only)
   └────┬────┘
        ▼
   Reciprocal rank fusion (k = 60)
        │
        ▼
   Exact-citation priority (provision-level citations only, §7.4)
        │            [cross-encoder rerank: off by default, eval arm only]
        ▼
   Matched sub-chunk(s)
        │
        ▼
   Parent resolution → Full canonical provision(s), each once
        │
   ┌────┴─────────────┐
   ▼                   ▼
Cross-reference     Metadata/temporal
traversal (bounded) re-check
   │                   │
   └────────┬──────────┘
            ▼
       Evidence set
            │
            ▼
   ApplicabilityRule matching (GroundedDetermination)
            │
            ▼
       Generation
```

---

## 18. Evaluation Strategy and Metrics

Four evaluation surfaces: extraction, retrieval, determination, end-to-end. **Numerical
acceptance thresholds are TBD as of this spec and must be established as measured
baselines during Phase 0, then locked before V1 is considered complete** — "above an
agreed threshold" is not itself an acceptance criterion; the agreed threshold must exist
as a number before Phase 5 sign-off. The metric categories below are locked now; the
target values are not.

**Retrieval:**
- Recall@k, Precision@k (k TBD, likely k∈{5,10})
- Hybrid vs. vector-only delta on both metrics — the comparison itself, not just
  absolute numbers, is the point (§13.2's candidate-starvation claim is only
  demonstrated if hybrid+temporal-filter measurably beats vector-only+no-filter)
- With vs. without reranking, same metrics

**Retrieval — Phase 1 measured results and locked thresholds (2026-10-02).**
Harness: `eu-ai-check eval-retrieval`; set: `data/eval/retrieval_queries.json`.
Relevance is scored at `versioned_provision_id` level, so the right article in the
wrong version is a miss.

> **Post-hoc queries — read this before any number below.** The original set had
> 65 queries (20 `terminology`, 28 `use_case`, 17 `temporal`), written before any
> run and frozen with its results in `data/eval/results/baseline_v1_65q*`. Its
> terminology queries carried topical words, so every arm — vector-only included —
> scored Recall@5 = 1.0 and the hybrid-vs-vector comparison was untestable. **33
> citation queries were therefore added *after* the first run had been seen**:
> `terminology_bare` (26 bare citations such as "Article 26(7)") and
> `terminology_variant` (7 other citation styles such as "Art. 26(6)"). Every one
> is flagged `post_hoc` in the file. Ten of them (B01–B10, `seen_in_probe`) were
> also run in an informal probe before being added; the other 23 were written
> without being run. The harness always reports the 48 pre-registered real-corpus
> queries as their own slice, so a post-hoc gain cannot be read as planned.

Measured (rerank off unless named; full tables in
`data/eval/results/expanded_v2_98q.txt`):

| slice | vector | BM25 | hybrid (RRF) | **hybrid + citation priority (design)** |
|---|---|---|---|---|
| bare citations, R@5 (n=26, post-hoc) | 0.731 | 0.962 | 0.865 | **1.000** |
| bare citations, P@5 | 0.146 | 0.208 | 0.177 | **0.215** |
| — of which never run before (n=16), R@5 | 0.750 | 0.938 | 0.781 | **1.000** |
| other citation styles, R@5 (n=7, post-hoc) | 0.571 | 1.000 | 0.857 | 0.857 |
| use case, R@5 (n=28, pre-registered) | 0.929 | 0.857 | 0.946 | **0.946** |
| all pre-registered, R@5 (n=48) | 0.958 | 0.917 | 0.969 | **0.969** |

Precision@k is reported but is the weaker signal here: most queries have exactly
one relevant provision, so P@5 is capped near 0.2 and P@10 near 0.1.

**Locked Phase 1 thresholds — regression guards, NOT pre-registered acceptance
criteria.** These were set *after* the measurements above were seen, on queries
that were themselves added post-hoc. They guard against the design regressing; they
are not evidence that a pre-planned hypothesis was confirmed, and must not be
presented as such.

- On `terminology_bare`: design Recall@5 ≥ vector-only + **0.15** (measured
  +0.269), and Precision@5 ≥ vector-only + **0.03** (measured +0.069).
- On `use_case` (pre-registered): design Recall@5 ≥ vector-only (measured 0.946
  vs 0.929) — citation handling must not cost natural-language retrieval.
- On `temporal`: pre-filter returns zero ineligible versions at every k, and an
  empty result before entry into force (hard, not a threshold).

A genuinely pre-registered test of the citation design needs citation queries
written *before* the next run; new query sets should be committed before they are
evaluated.

**Re-check on the Phase 3 corpus (107 provisions, 2026-10-03).** The corpus fixes
and Annex I (§9.1) added 22 provisions, and none of the existing ones changed. The
same 98 queries were re-run unchanged. Results are in
`data/eval/results/corpus_v2_107p_98q.*`; the Phase 1 files stay frozen.

| slice | vector | hybrid (RRF) | **design** | design, Phase 1 |
|---|---|---|---|---|
| bare citations, R@5 | 0.654 | 0.827 | **1.000** | 1.000 |
| bare citations, P@5 | 0.131 | 0.169 | **0.215** | 0.215 |
| use case, R@5 | 0.929 | 0.946 | **0.946** | 0.946 |
| all pre-registered, R@5 | 0.948 | 0.958 | **0.969** | 0.969 |

- **All three regression guards still hold.** On bare citations, R@5 is +0.346 over
  vector-only and P@5 is +0.084. On use case, the design scores 0.946 against 0.929.
  The temporal results did not change.
- **The design's own numbers are unchanged at R@5**, and use-case R@1 rose from 0.679
  to 0.750. Vector-only lost ground on bare citations (0.731 → 0.654) and on
  pre-registered queries (0.958 → 0.948). The new Annex I entries are long,
  boilerplate-heavy legislative titles that crowd the top-5 for vague queries
  (misses B03, B25, V04).
- **Caveat:** none of the 98 queries targets an Annex I entry or one of the new
  subparagraphs, so this re-check measures distraction, not retrieval of the new
  provisions. The matcher evaluates those scopes exhaustively (§4.2), so it does not
  depend on retrieving them.
- **Article 3(3), (4), (23) added (110 provisions).** Results are in
  `data/eval/results/corpus_v3_110p_98q.*`. Recall@5 and Precision@5 are unchanged
  for every arm on every slice. The design's use-case R@1 rose again, from 0.750 to
  0.786. The new definitions appear as BM25 distractors (Article 3(3) in U04, U06 and
  U07) but displace no correct result from the design's top-5. The guards hold.

**Extraction:**
- Field-level accuracy (semantic match for free-text fields: purpose, functionality)
- Exact-match for deterministic fields (dates, boolean actor-fact fields)
- **Zero-tolerance metric, not a threshold:** extraction must never populate an
  excluded/conclusion-level field (risk_tier, prohibited_practice, actor_roles,
  substantial_modification) — any occurrence in the eval set is a hard failure, not a
  score to optimize.

**Extraction — Phase 2 pre-registration (2026-10-02).** Learning from Phase 1, the
extraction eval set (`data/eval/extraction_cases.json`, 30 hand-labelled cases), the
scoring rules, the leakage term list and the extractor were **committed before the
first API run**, so the first results count as pre-registered. Scoring, as committed:

- booleans and dates: exact match, `null` included (`null` ≠ `false`);
- free text, **primary**: hand-written concept groups (every group matched by at least
  one stem; an expected `null` must be predicted `null`);
- free text, **secondary**: an LLM judge compares each value with the label's
  reference sentence. It never overrides the primary metric, and every disagreement
  is listed. The pre-registered judge was Claude Opus 5.5; it was migrated to OpenAI
  GPT-6 Astra before any API call, with the same prompt and verdict schema (§3.5.2);
- zero tolerance: no excluded field *and* no conclusion term
  (`src/eu_ai_check/extraction/leakage.py`) in any free-text value. Structured
  outputs prevent the first; the second is the realistic failure, and 5 adversarial
  cases assert conclusions outright to provoke it.

Each case runs three times per effort arm (`low`, `medium`), and fields whose
correctness changes between repeats are reported as unstable. **LLM stack: OpenAI
only** — extractor GPT-6.1 Sol (`gpt-6.1-sol`), optional judge GPT-6 Astra
(`gpt-6-astra`); only `OPENAI_API_KEY` is needed. Both were switched from Claude
Opus 5.5 after pre-registration and before any API call; the evaluation is unchanged
(§3.5). The numerical accuracy target is locked only after the first run is seen,
and is labelled accordingly.

**Extraction — Phase 2 first run (pre-registered, 2026-10-02).** Run with the
committed defaults (30 cases × 3 repeats × effort `low`/`medium`, judge on) and frozen
unchanged in `data/eval/results/extraction_v1_30c.{txt,json,runs.jsonl}`. No label,
scoring rule, prompt or case was changed before or after the run.

| arm | overall | booleans | dates | free text | unstable | output tokens |
|---|---|---|---|---|---|---|
| `low` | 0.967 | 0.984 | 1.000 | 0.942 | 4 | 11.5k |
| `medium` | 0.959 | 0.982 | 1.000 | 0.927 | 5 | 18.5k |

180/180 runs succeeded, with 0 leakage hits and 0 fallbacks. `medium` scored no higher
than `low` (8 fewer correct field scores out of 990) and used about 60% more output
tokens. No significance test was pre-registered, so this difference is reported as
observed, not as a finding. The weakest field in both arms is `affected_persons`
(0.833 / 0.811). Secondary judge agreement with the primary score was 268/298
(`low`) and 263/299 (`medium`). **No accuracy target had been locked at this point**
(it was locked later; see "Phase 2 acceptance" below), and any later label or scoring
change will be flagged as post-hoc.

**Extraction — follow-up experiment `role-null-v1` (pre-registered 2026-10-02, before
any follow-up API call).** This experiment is separate from the frozen first run.
It tests one prompt-only intervention suggested by the error analysis of that run.
The frozen eval set, labels, scoring rules, `FIELD_RULES` and the v1 results are all
unchanged; tests pin the eval content, the baseline prompt's SHA-256 and the v1
results file.

- *Intervention.* `ROLE_NULL_V1` in `src/eu_ai_check/extraction/extractor.py` is
  appended after FIELD RULES as a `CLARIFICATION: role_in_decision_making` section,
  selected with `--prompt-variants role-null-v1`. The default, `baseline`, is the
  frozen prompt. The clarification restates L8's condition and does not change the
  definition: fill the field only when the description says what happens with the
  output, and never fill it just to say the role is not described. Its two examples
  appear in no eval case.
- *Hypothesis.* Most role false fills on cases with a null role label come from two
  habits: restating the system's task as its role, and filling the field just to say
  the role is not described. Stating L8's condition explicitly removes those habits
  without causing nulls where a role is described.
- *Target cases.* E09 and E14 are clear extractor errors in the baseline, at medium
  only. E20's outcome is uncertain and its label possibly ambiguous. E06 is not
  targeted, and turning it null would be read as over-correction. The guard is the
  17 cases with a non-null role label, especially E07, E12, E25, E26 and E29, where
  the system acts by itself. `affected_persons` is deliberately not addressed and
  serves as a no-regression field.
- *Design.* 4 arms: {baseline prompt, `role-null-v1`} × {`low`, `medium`}, each
  30 cases × 3 repeats, for 360 extraction calls. The judge is on, with the
  baseline's configuration. Results go to
  `data/eval/results/extraction_v2_role_null_*`. The frozen v1 run stays the
  reference; the baseline re-run only measures run-to-run noise.
- *Metrics.* These are fixed in code in `src/eu_ai_check/extraction/followup.py`
  and checked against the frozen run:

  | metric | measured on | frozen low / medium |
  |---|---|---|
  | role false fills | 13 null-role cases, 39 runs per arm | 5/39 / 9/39 |
  | of which E09+E14 | 6 runs | 0/6 / 3/6 |
  | absence-only fills (`ABSENCE_RE`, descriptive) | filled values on null-role cases | 2 / 4 |
  | guard misses | 17 non-null-role cases, 51 runs | 0/51 / 0/51 |
  | other fields | per-field accuracy | first-run table above |
  | leaks | all runs | 0 / 0 |

- *Decision rule (fixed now; it may not change after results are seen).*
  `role-null-v1` counts as having worked only if all four checks hold at both
  efforts:
  1. total false fills fall below the frozen run's, and E09+E14 false fills do not
     rise;
  2. guard misses rise by at most 1 over the frozen run, with every new miss listed;
  3. for every scored field other than `role_in_decision_making`,
     |acc(role-null-v1) − acc(frozen)| ≤ |acc(baseline re-run) − acc(frozen)|. A
     movement in either direction counts;
  4. zero leaks in the `role-null-v1` arms.

  Results are reported per arm, per case and per repeat, with E09/E14 separate from
  E06/E20. With 39 runs per arm the result shows the direction of an effect, not
  statistical significance. Even if the rule passes, adopting the prompt and changing
  the default effort remain separate decisions.
- *Separate POST-HOC analysis (not pre-registered).* `affected_persons` in the
  frozen run is re-scored so that a value also counts as correct when the secondary
  judge accepted it (`followup.posthoc_affected_persons`). It uses no API calls and
  never replaces the frozen scoring rules.

**Extraction — follow-up `role-null-v1` result (2026-10-02): the decision rule was
NOT met.** The run used the pre-registered design: 360 calls, all succeeded, 0 leaks.
Results are in `data/eval/results/extraction_v2_role_null.{txt,json,runs.jsonl}`,
and the decision report, with every value per case and per repeat, is in
`extraction_v2_role_null_decision.{txt,json}`. The prompt was **not adopted**. The
default prompt (`baseline`), the default effort and the official v1 evaluation are
unchanged.

| check | `low` | `medium` |
|---|---|---|
| 1 false fills down, E09+E14 not up | pass (5→2; 0→0) | pass (9→2; 3→0) |
| 2 guard: ≤ 1 new miss | pass (0→1) | **fail** (0→3) |
| 3 other fields within re-run noise | **fail** | **fail** |
| 4 zero leaks | pass | pass |

- **Improved:** E09 and E14 were clean in all 12 `role-null-v1` runs. The baseline
  re-run showed both errors also occur intermittently at `low`. Values that only
  say "not described" fell to 0.
- **Key regression, E27** (a guard case with role label "assists judges, who
  decide"). The new prompt left the role null for "helps judges by summarising case
  law": 3/3 runs at `medium` and 1/3 at `low`. It read the description as a task
  description alone.
- **Over-correction, E06.** The role became null in 6/6 runs. That matches the
  label, but the pre-registration named this in advance as an over-correction
  signal, since the description says border guards use the kiosk to confirm identity.
- **E20** stays mostly filled ("the model is used to set individual premiums"), now
  without the absence clause.
- **Check 3** compares each field's movement with the baseline re-run's gap, which
  was 0.000 on every failing field, so any single change fails. Three of the four
  failing moves were improvements: `low` ai_functionality +0.011 and affected_persons
  +0.022; `medium` intended_purpose +0.022. The fourth was a regression: `medium`
  relevant_date −0.011 (E27, assessment date given where the label is null). The
  rule is applied as written.

**POST-HOC (not pre-registered, not part of the official evaluation):
`affected_persons` re-score of the frozen v1 run.** A value also counts as correct
when the secondary judge accepted it. This raises the apparent score from 0.833 to
0.878 at `low` and from 0.811 to 0.889 at `medium`. Every remaining miss is an empty
answer where the label infers the group (E16, E26, E28, and partly E10/E11). The
**official v1 scores stay 0.833 / 0.811**, and this re-score does not change them
(`data/eval/results/posthoc_affected_persons_v1_30c.txt`).

**Reporting incident and known bug (maintenance, not yet fixed).**
`followup.py --out` builds file names with `Path.with_suffix`. A dotted output name
(`extraction_v2_role_null.decision`) therefore became `extraction_v2_role_null.txt`
and `.json`, and overwrote the harness summaries. The raw `.runs.jsonl` (360 records)
was never affected. Both summaries were rebuilt offline from it. Rebuilding v1 the
same way reproduced the committed `extraction_v1_30c.json` exactly, and
`extraction_v1_30c.txt` identically apart from line endings. The decision report was
regenerated under the undotted name `extraction_v2_role_null_decision`. Until it is
fixed, pass `--out` an undotted name.

**Extraction — Phase 2 acceptance (2026-10-02): MET.**

- *Accepted configuration.* `gpt-6.1-sol`, `baseline` prompt, `reasoning.effort =
  low`. This is an engineering choice based on the observed results and lower output
  token use: `low` used 11.5k output tokens vs 18.5k for `medium` in v1. It is **not**
  a claim that `low` is more accurate: the baseline re-run scored 0.967 at both
  efforts. The judge is evaluation-only and is not part of the production extraction
  path.
- *Baseline.* Phase 0 contained **no extraction accuracy measurement**; it deferred
  the number to Phase 2 (`src/eu_ai_check/corpus/baseline.py`). The "Phase 0
  baseline" in the acceptance criterion is therefore the frozen v1 run (`82467bb`),
  the committed Phase 2 baseline used for this target check.
- *Locked target.* Overall field-level accuracy **≥ 0.95**, measured with the
  pre-registered primary concept-group scorer on the frozen 30-case v1 eval set, in
  the accepted `low` configuration. **This target was locked after the first
  evaluation results were seen**, which the Phase 2 pre-registration permits ("locked
  only after the first run is seen, and labelled accordingly"). It is **not** a
  pre-registered target. Like Phase 1's locked thresholds, it serves as a regression
  guard and does not independently confirm the result.
- *Zero tolerance.* No excluded field and no legal-conclusion term in any value.

| criterion | committed result | outcome |
|---|---|---|
| overall field accuracy ≥ 0.95 (`low`, v1 `82467bb`) | 0.967 | met |
| zero leaks | 0 in v1 (180 runs); also 0 in all 360 v2 runs | met |

The post-hoc `affected_persons` re-score is **not** used for acceptance. The official
v1 scores are unchanged. The implementation default now matches the accepted
configuration. `DEFAULT_EFFORT = "low"` drives `extract --effort`,
`create_extractor` and `OpenAIExtractor`, and tests pin it. `--effort medium`
remains available. The eval harness still runs both pre-registered arms by default.

**Determination:**
- Correctness against the four-state contract (§14): `applicable` /
  `not_applicable` / `unresolved` / `not_in_force`, scored per state, not just overall
  accuracy — a system that's accurate on `applicable` cases but wrong on
  `not_in_force` vs. `not_applicable` discrimination has a real defect that an
  aggregate score would hide.
- **Zero-tolerance metric:** no `unresolved` case in the eval set may be silently
  produced as `not_applicable` or `applicable`. No `not_in_force` case may be produced
  as `not_applicable`. Both are hard failures (§14's invariant), not threshold misses.
- **Matcher vs score-cutoff baseline [Phase 3 design, 2026-10-03]:** both arms run on
  the same determination eval set, scored per state as above. Also reported: the
  matcher's verification-downgrade rate (§4.2), and `system_category` / `derogation`
  correctness for risk-tier cases. The matcher is adopted only if it beats the
  baseline under a decision rule fixed in the Phase 3 pre-registration, the same
  discipline as Phase 1 (invariant 20).

**Determination — locked targets, fixed BEFORE the first run [Phase 3 design,
2026-10-03].** Phases 1 and 2 locked their targets after seeing results, and both are
recorded as regression guards rather than pre-registered criteria. Phase 3 fixes its
numbers now, before the eval set is run even once. A miss is then reported as a miss,
not retuned.

- **Inputs:** the determination stage is scored on **gold `Case` inputs** (hand-labelled
  facts plus the original description), not live extraction. A miss is then
  attributable to this stage (see below). End-to-end scoring is Phase 5.
- **Zero tolerance (hard failures):**
  - `unresolved` produced as `applicable` or `not_applicable`;
  - `not_in_force` produced as `not_applicable`;
  - a positive conclusion resting on a verdict that failed code verification (§4.2).
- **Rule-based outputs:** actor role, obligation applicability, `not_in_force`, and
  `not_applicable_reason` on rule-backed outcomes must score **1.00**. With gold
  inputs these are deterministic code over §12 / §14.2, so anything less is a bug,
  not a tolerance.
- **Matcher-backed outputs:** risk tier and prohibited practice need per-state
  accuracy **≥ 0.85**, separately on `applicable`, `not_applicable` and `unresolved`,
  for the adopted matcher arm. This is a judgement fixed before any data, sized for a
  small hand-built set: roughly one miss per ten cases in each state.
  `system_category` and `derogation` are scored on risk-tier cases and reported. They
  are not separately gated in V1.
- The matcher must **also** beat the score-cutoff baseline under the pre-registered
  decision rule. Clearing 0.85 alone does not adopt it.

**Determination — Phase 3 pre-registration [2026-10-03].** The eval set (labels
reviewed and approved by the user), arms, scoring and decision rule below are
committed and binding. The τ values, the matcher prompt and the verification code
were committed afterwards, still before the first determination run (Phase 2
precedent; see "Pre-registration completed" below).

- **Eval set.** `data/eval/determination_cases.json`: 41 hand-labelled cases with gold
  facts plus the description. Labelling decisions DL1–DL5 are recorded in the file.
  Once committed, its content is SHA-pinned in tests. A test also recomputes every
  rule-based label (roles, Article 16/26) from the gold facts and the labelled risk
  tier, so a labelling slip cannot survive as "ground truth".

  | Expected state | Risk tier | Prohibited practice |
  |---|---|---|
  | `applicable` | 21 | 7 |
  | `not_applicable` (`conditions_not_met` / `not_yet_applicable`) | 14 (11 / 3) | 29 (28 / 1) |
  | `unresolved` | 5 | 4 |
  | `not_in_force` | 1 | 1 |

  Coverage required by PLAN §8:
  - simultaneous provider+deployer (D02);
  - Article 25 transfer (D34–D36);
  - the same provision at three dates (Article 26: D38 / D39 / D01);
  - the Article 6(3) derogation (D09);
  - the 6(1) vs 6(2) routes (D18–D19 vs D01);
  - `not_in_force` vs `not_applicable` on one provision (D38 vs D39).
- **Arms.**

  | Arm | Model and effort | Role | Runs per case |
  |---|---|---|---|
  | **M-low** | matcher, `gpt-6.1-sol`, effort `low` | primary (the accepted Phase 2 configuration) | 3 |
  | ~~M-medium~~ | ~~matcher, effort `medium`~~ | ~~reported only~~ | dropped, see below |
  | B | score-cutoff baseline | comparison | 1 (deterministic) |

  For the matcher arms, per-state accuracy is the mean over runs.

  **[Pre-run amendment, 2026-10-03, before any determination API call]** The M-medium
  arm is dropped for cost. Its estimated share was about $11–17 of a $20–30 run. It
  was "reported only": the decision rule compares M-low with B and never reads
  M-medium. Dropping it changes neither the decision rule, nor the targets, nor any
  label. The run is therefore M-low × 3 repeats (123 calls) plus B. Before the run, one
  smoke-test call is made with `eu-ai-check match` on a made-up description that is
  not in the eval set. It confirms that the API accepts the strict schema and measures
  the real token usage. That call is not a result on the eval set.
- **Baseline score and τ.**
  - Score: the maximum cosine similarity (bge-base-en-v1.5, the Phase 1 embedder)
    between the description and any chunk of an in-scope provision. Cosine is used
    rather than the RRF score because RRF is rank-based and carries no
    match/no-match magnitude.
  - Verdicts: in each matcher scope (Annex III, Article 5(1), 6(3)(a)–(d), the 6(3)
    override, Annex I, 6(1)(b)), the top candidate is `matches` if its score is ≥ τ,
    and every other candidate is `does_not_match`. The verdicts then go through the
    same §9.1 combination code as the matcher's.
  - τ per scope: chosen by maximising Youden's J on the Phase 1 retrieval eval set.
    A use-case query is a positive for a scope when its relevant provisions include
    an in-scope one, and a negative otherwise.
  - Scopes with no positives in that set (Annex I, the override, 6(1)(b)) use one
    pooled τ fitted across all scopes.
  - The values are computed and committed before the first determination run.
- **Scoring.** A status is correct only on an exact match, *including* the
  `not_applicable_reason`. The per-state buckets are `applicable`, `not_applicable`
  (both reasons) and `unresolved`. `not_in_force` cases are gated at 1.00 as
  deterministic outputs, not as matcher states.
- **Decision rule (adopt the matcher, M-low, over B).** All three must hold:
  1. Zero-tolerance checks pass in every M-low run.
  2. M-low meets the locked targets: ≥ 0.85 per state on risk tier and on prohibited
     practice, and 1.00 on rule-based outputs.
  3. M-low beats B by ≥ 0.10 on macro per-state accuracy (the mean over the three
     buckets), for risk tier and for prohibited practice separately. M-low must also
     be ≥ B on the `applicable` bucket and on the `not_applicable` bucket
     individually. Without that last condition, the win could come entirely from B
     being structurally unable to produce `unresolved`.

  If 2 fails, Phase 3 acceptance is not met. Any prompt change becomes a new
  pre-registered follow-up, as `role-null-v1` was in Phase 2, and is never a retune
  against this set. If 1 and 2 hold but 3 fails, the result is reported and the
  matcher-vs-baseline design question reopens.
- **Pre-registration completed (2026-10-03, before any determination run).**
  - *Code:* `src/eu_ai_check/determination/` and `eu-ai-check eval-determination`.
  - *One matcher call per case* covers every scope: 59 date-eligible candidates
    (25 Annex III, 8 Article 5(1), 4 + 1 Article 6(3), 20 Annex I, 1 Article
    6(1)(b)), roughly 8.5k input tokens. No call is made when no candidate text is in
    force; the case is then `not_in_force` without asking the model.
  - *Prompt:* the matcher prompt, response schema and scope questions are SHA-pinned
    in `tests/test_determination.py`. One clarification was added before pinning:
    a description of what a system *is* can answer a question "no" (a spam filter is
    evidently not a machinery safety component). Without it, the silence rule could
    make every non-product case `unresolved` through the 20 Annex I candidates.
  - *τ values:* `eu-ai-check fit-tau` → `data/eval/determination_tau.json`.

    | Scope | τ | Source |
    |---|---|---|
    | Annex III | 0.5052 | 14 positives / 14 negatives |
    | Article 5(1) | 0.5362 | 7 / 21 |
    | 6(3) conditions | 0.6388 | 1 positive only, fitted as the rule says |
    | Override, Annex I, 6(1)(b) | 0.4883 (pooled) | no positive queries |

  - *Rule path:* scored on the labelled risk tier as pre-registered, it reproduces
    all 123 rule-based labels (1.00). That is checked offline in the test suite and
    again in every run.
- **Small-n, stated in advance.** `unresolved` has 5 risk-tier and 4 prohibited
  cases. With 3 runs each, that is 15 and 12 judgements, so 0.85 permits at most 2
  and 1 wrong runs respectively. A single systematically mislabelled or misjudged case
  can sink the bucket. That is accepted as the price of a hand-built set, and is said
  here so it cannot be discovered conveniently later.

**Determination — Phase 3 first run (pre-registered, 2026-10-04).** A one-call smoke
test with `eu-ai-check match` on a made-up description outside the eval set came
first. The API accepted the strict schema, and all 59 candidates were
`does_not_match`, which is correct. That call cost $0.057. The pre-registered run
then went ahead as amended: M-low × 3 (123 calls) plus B, with no label, prompt,
scoring rule or case changed.

- *Raw first attempt, frozen unchanged:*
  `data/eval/results/determination_v1_41c.{txt,json,runs.jsonl}`. 26 of the 123
  M-low calls (21%) failed with `APIConnectionError: Connection error.`. They are
  D30#1–2, D31, D32, D34#0–1, D35#2, D36, D37, D39, D40 and D41, all repeats unless
  marked. They fall in the last stretch of the run, which points to a network
  outage. The harness scored each one as wrong. B had no errors.
- *Retry of the API-failed calls only (user decision, 2026-10-04).* The
  pre-registration did not say how to handle transport failures. Only the 26 failed
  (case, repeat) pairs were re-called, once each, with the same model, effort,
  prompt and labels. The 97 successful M-low runs were not re-called: they were
  replayed through the pipeline from their recorded verdicts, and the script stops
  unless each replay reproduces its original record exactly. B was recomputed under
  the same exact-match check. All 26 retries succeeded. Script:
  `scripts/retry_determination_failed.py`. Merged result:
  `data/eval/results/determination_v1_41c_retry.{txt,json,runs.jsonl}`. Cost: $5.44
  for the first attempt and $1.42 for the retry.
- *The merged run is the official Phase 3 result.* The first attempt is kept only
  as the raw record.

**Determination — Phase 3 acceptance (2026-10-04): NOT MET** against the
pre-registered decision rule.

| | M-low | B | target |
|---|---|---|---|
| risk tier: applicable / not_applicable / unresolved | 1.000 / **0.786** / 0.933 | 0.524 / 0.714 / 0.000 | ≥ 0.85 each |
| prohibited: applicable / not_applicable / unresolved | 1.000 / **0.632** / 1.000 | 0.571 / 0.828 / 0.000 | ≥ 0.85 each |
| macro: risk tier / prohibited | 0.906 / 0.877 | 0.413 / 0.466 | M-low ≥ B + 0.10 |
| zero-tolerance violations | **1** | 9 | 0 |
| rule path | 1.000 (123 checks) | | 1.00 |

Condition 1 fails, and so does condition 2. Condition 3 fails on one sub-check only:
prohibited `not_applicable`, where M-low is below B. The matcher is **not adopted**
on this result. Root cause, from the recorded verdicts:

- *Zero tolerance, D32#2.* Risk tier is labelled `unresolved` and came back
  `not_applicable`. On that repeat the matcher returned `does_not_match` for Annex
  III 1(c), where the other two repeats returned `insufficient`. This is run-to-run
  variance; D32#0 and #1 are correct.
- *prohibited `not_applicable` = 0.632 (32 misses out of 87).* All 32 came back
  `unresolved`. In 30 of them, Article 5(1)(c) (social scoring) came back
  `insufficient`, alone in 26. The cases are D02, D04, D05, D07, D11, D12, D33–D36
  and D39, and none of them describes social scoring. A typical rationale (D04,
  exam proctoring) is: "The description does not establish social scoring or the
  nature and proportionality of any resulting treatment". The matcher treats an
  absence of evidence *against* 5(1)(c) as missing information. The other 2 misses
  are D08#2 (5(1)(a)–(b)) and one 5(1)(f)-only miss.
- *risk tier `not_applicable` = 0.786 (9 misses out of 42).* These are D10, D26 and
  D28, three repeats each. **Article 5(1)(c) over-caution does not cause them**:
  - D26: the 5(1)(c) match is correct, and prohibited is correctly `applicable`. The
    risk-tier miss comes from a further Annex III 4(a) match ("employers use the
    scores to deny jobs").
  - D28: prohibited is correctly `applicable` (5(1)(b)). The risk-tier miss is Annex
    III 1(b) at `insufficient` ("does not say whether categorisation is based on
    biometric data").
  - D10: Annex III 4(b) at `insufficient` (are the loan officers monitored?), and the
    6(3) profiling question also `insufficient`. Gold: Annex III 5(b) with the
    derogation applied.

**Determination — post-hoc intervention on Article 5(1)(c) (opened 2026-10-04).**
This is a **POST-HOC** intervention, documented with the same discipline as Phase 2's
`role-null-v1`. It differs from the decision rule's own instruction ("never a retune
against this set") in one respect, and is labelled accordingly:

- *The 41-case set becomes the DEV set for this intervention.* It has been inspected
  and the error analysis above was done on it. Any further measurement on it is
  **diagnostic only, never validation**. The official Phase 3 result above stays
  NOT MET whatever the dev-set numbers show.
- *Validation* happens only on a new held-out confirmation set built before the
  iteration starts and not touched during it. It gets one pass, and its results are
  reported separately from the dev set and never merged into a combined number.
- *Scope:* the 5(1)(c) over-caution pattern only. The risk-tier misses above (D10,
  D26, D28) and the D32#2 variance have different causes and are outside this
  intervention.
- *Held-out set, frozen before any matcher change (`6b98c76`).*
  `data/eval/determination_heldout_5_1_c.json` has 8 cases, from angles not in the
  failing dev cases:
  - H01 payment fraud, H02 workplace productivity, H03 churn, H04 motor telematics
    and H05 buy-now-pay-later credit: prohibited `not_applicable`.
  - H06 an employer scoring private social-media behaviour: the positive control,
    5(1)(c) `applicable`.
  - H07 and H08: borderline, prohibited `unresolved`.

  The rule-based labels were checked offline (24/24). **This set was never run**,
  because the intervention stopped on the dev set. It stays untouched and could
  serve as a confirmation set for a future, separately pre-registered change.
- *Hard stop, set by the user before v2 ran:* v2 is the last dev iteration. A
  zero-tolerance violation or a clear regression on the dev set ends the
  intervention as NOT ADOPTED, with no v3.

**Variant `social-scoring-v1` (`5b5d186`).** One rule was appended to the
pre-registered system prompt. It said that an evaluation of people used for its
own stated purpose, in the same context as its data, is lawful purpose-specific
evaluation, not social scoring, so the answer is `does_not_match`. It added that
the description need not rule out every element of the provision, and that
`insufficient` is kept for cases where the use of the score or the resulting
treatment is left open. No domain examples were given. The variant text is
SHA-pinned in `tests/test_determination.py`.

**Variant `social-scoring-v2` (`897c22e`).** v1's rule sat in the system prompt
and spilled over to other provisions. For v2, the restriction was made structural:
the system prompt is byte-identical to the pre-registered one, and the guidance is
printed only as a `note:` line inside the Article 5(1)(c) candidate's block of the
user message. The note opens with "Guidance for this candidate only, Article
5(1)(c) social scoring; it does not apply to any other candidate". Its text is
SHA-pinned, and a test checks that it appears exactly once, under
`aiact:art_5:para_1:point_c`.

**Dev-set results (41 × 3 M-low, diagnostic only, never validation).**

| | official (pre-registered prompt) | v1 | v2 |
|---|---|---|---|
| risk tier: applicable / not_applicable / unresolved | 1.000 / 0.786 / 0.933 | 1.000 / 0.786 / 0.800 | 1.000 / 0.786 / 0.733 |
| prohibited: applicable / not_applicable / unresolved | 1.000 / 0.632 / 1.000 | 1.000 / 0.931 / 1.000 | 1.000 / 0.954 / 0.667 |
| 5(1)(c) `insufficient` on cases that are not social scoring | 30 runs | 1 of 105 | 3 of 105 |
| zero-tolerance violations | 1 (D32 risk tier, 1/3) | 3 (D32 risk tier, 3/3) | **4 (D32 risk tier 2/3, D32 prohibited 2/3)** |
| API errors | 0 after retry | 0 | 2 (D41#1–2, connection; not retried) |
| cost | $6.86 | $6.89 (+ $0.78 for a 1-repeat subset run) | $6.83 |

Results are in `data/eval/results/dev_social_scoring_v{1,2}_41c.*`, plus
`dev_social_scoring_v1_iter1.*` (18 cases × 1 repeat). In v2, the 2 D41 errors count
as wrong in the unresolved buckets. They were not retried because the D32
violations already decide the outcome. The "decision rule" block in these files
reads `decided: false` only because the harness looks for an arm named
`matcher-low`. These runs are not a decision-rule evaluation.

- *v1:* it fixed the target pattern (prohibited `not_applicable` 0.632 → 0.931), but
  it spilled over. D32's Annex III 1(c) went `does_not_match` in 3/3 runs ("attention
  is a cognitive state, not an emotion"), up from 1/3. D24's Article 5(1)(e) went
  from `does_not_match` to `insufficient` in 3/3.
- *v2:* D24 is clean in 3/3, but D32 is worse. The note misfires *inside its own
  scope*. D32 ("estimates students' attention levels from webcam images during
  online classes") states no use of the score, which is exactly the case the note
  reserves for `insufficient`. Yet in 3/3 runs the model answered `does_not_match`
  on 5(1)(c), citing the note: "purpose-specific attention evaluation using data
  from the same classroom context, not social scoring under the supplied guidance".
  In 2/3 runs, 5(1)(f) and Annex III 1(c) were also `does_not_match`, so both D32
  determinations became `not_applicable`: two zero-tolerance violations per run.
  Prohibited `unresolved` fell from 1.000 to 0.667, partly because of the D41
  errors. Scoping the guidance to one candidate's block therefore did not stop the
  general "purpose-specific, so negative" reading from spreading to the case as a
  whole.
- *Unchanged throughout:* D10, D26 and D28 (risk tier) and D04 (now on 5(1)(f)).

**Verdict: `social-scoring-v1` and `social-scoring-v2` are NOT ADOPTED** (hard
stop: v2 has zero-tolerance violations on the dev set). The pre-registered prompt
stays the default. The variants remain in the code only behind
`--prompt-variant`, as Phase 2 did with `role-null-v1`. **Phase 3 acceptance stays
NOT MET**, as recorded above. What has been learned, for any future
pre-registered attempt:

- The 5(1)(c) over-caution is real and responds to guidance.
- The guidance must not supply a reason to answer `does_not_match` that the model
  can apply where the description is silent. Both phrasings did, by different
  routes.
- D32 (attention vs emotion recognition) is the most fragile case in the set, and
  any change has to be measured against it.

**Generation — Phase 4 pre-registration (APPROVED 2026-10-06).** Design:
`docs/phase4_design.md`; contract: §16.2–16.3. Everything below was approved by the
user and committed **before any Phase 4 API call** (no smoke test, no generation).
It is binding. As with Phase 3, a miss is reported as a miss and is not retuned.

- **Inputs, not a pipeline run.** The generator is developed and scored on the cached
  official Phase 3 run (`determination_v1_41c_retry`, M-low), replayed through
  `Pipeline` and the adapter at zero cost. Phase 4 metrics are measured against the
  **input** determination, never the gold label (invariant 26).
- **Split, frozen before any generator prompt existed.**
  `data/eval/generation_split.json`, SHA-pinned in `tests/test_generation_split.py`
  and built by `scripts/make_generation_split.py`. The 41 cases are split by case
  id, stratified on the gold risk-tier state, with seed 20261006. D38 is forced into
  held-out because it is the only `not_in_force` case. Every repeat of a case stays
  on one side.
  - *Dev, 28 cases / 84 records:* D01, D03, D04, D07–D12, D14, D16–D19, D21, D23–D26,
    D29, D30, D32–D34, D37, D39–D41.
  - *Dev, synthetic, 3 records:* S01 (D01 at 2024-07-31), S02 (D21 at 2024-03-13)
    and S03 (D30 at 2023-06-01). These are dev cases re-dated before entry into
    force. `Pipeline` finds no candidate in force and never calls the matcher, so
    every determination is `not_in_force`, deterministically and at no cost (a test
    replays them with a matcher that raises). They are marked `synthetic`, are not
    Phase 3 results, and exist only so that `not_in_force` wording is seen in dev.
    Every provision has one start date, so they are all-`not_in_force`, like D38.
  - *Held-out, 13 cases / 39 records:* D02, D05, D06, D13, D15, D20, D22, D27, D28,
    D31, D35, D36, D38. Input determinations: 60 `applicable`, 45 `not_applicable`,
    42 `unresolved`, 15 `not_in_force`, plus 33 known role sets. Five of these cases
    (D02, D05, D28, D35, D36, all 15 of their records) carry at least one
    determination that differs from gold.
  - *Not used in Phase 4:* H01–H08 are reserved, untouched, for the Phase 5
    end-to-end evaluation.
- **Deterministic checks (no LLM), run on every answer.**

  | ID | Check |
  |---|---|
  | C0 | The response parses against the strict `GeneratedAnswer` schema; a refusal or an incomplete response fails. |
  | C1 | Every cited `evidence_id` exists in the input. |
  | C2 | Every cited `evidence_id` belongs to the claim's own determination, and that `determination_id` exists. |
  | C3 | A `quote` is a verbatim substring (after whitespace normalisation) of its cited excerpt. Every excerpt equals the corpus text of its `versioned_provision_id`. |
  | C4 | The rendered status equals the input status and reason (by construction, and tested). Claim text contains no status phrase from another state's lexicon. |
  | C5 | Every obligation article is rendered with its own evidence (by construction). Claim text names no provision it does not cite, and uses obligation language ("must", "shall", "is required to") only when it cites an obligation provision. |
  | C6 | Every input determination is rendered exactly once, and no claim references an id outside the input. |
  | C7 | Every `unresolved` determination renders its `unresolved_inputs` as a non-empty "we would need to know" list. |
  | C8 | No claim on a `not_in_force` determination uses the `not_applicable` lexicon. |

  C4, C5 and C8 are partly lexical, so a pass does not prove faithfulness. A failing
  answer is never shown: the template rendering replaces it, and the replacement is
  counted. The exact lexicons and regexes are fixed in code and tests before the
  smoke test.
- **Baseline: the template renderer** (no LLM, $0). It uses the same adapter, the same
  renderer and the same checks, with no model claims. It passes C0–C8 by construction,
  so check pass rates cannot tell the two arms apart.
- **Decision rule (APPROVED): adopt the LLM generator over the template only if all
  three hold on the held-out set.**
  1. *Hygiene:*
     - raw first-attempt pass rate on C0–C8 ≥ 0.95;
     - fallback rate ≤ 0.05;
     - zero C4 violations;
     - zero C8 violations.
  2. *Grounded-explanation coverage ≥ 0.90.* This is the share of explainable
     determinations that carry at least one model claim passing C0–C8. The
     explainable determinations are risk tier and prohibited practice when
     `applicable` or `not_applicable`, obligation articles when `applicable` or
     `not_yet_applicable`, and every `unresolved` determination. The template scores
     0.00 by construction. This criterion is deterministic.
  3. *Blind pairwise preference ≥ 15 of 20.* Ties count for the template. The
     protocol follows below.

  If any condition fails, the template ships as the Phase 4 generator, and the
  failure is recorded here.
- **Acceptance targets (APPROVED).**
  - Every shipped answer passes C0–C8: 1.00.
  - Input status reproduced in 100% of the 15 held-out records whose determination
    differs from gold. D32#2 (dev) is pinned by a regression test.
  - Reported, not gated: a human faithfulness read of the same 20 rated answers.
- **Rating protocol (frozen).** `data/eval/generation_rating_sample.json`, built by
  `scripts/make_generation_rating_sample.py` and SHA-pinned:
  - *The sample:* 20 held-out records, drawn before any generation existed with
    seed 20261007. Records are stratified by class, with proportional allocation,
    at least 2 per class and at most 2 per case. The classes are `not_in_force`,
    any `unresolved`, an obligation article that applies now or later, and
    otherwise `not_applicable`. The allocation is 2 / 9 / 4 / 5.
  - *Each pair:* the template and the LLM answer for the same input, labelled A and
    B. The A/B assignment was drawn from a fresh secret seed and written only to
    `data/eval/private/generation_rating_key.json`. That file is gitignored and is
    not opened until rating is finished. Its SHA-256 is committed in the sample
    file, and a test checks it, so the key is fixed now and can be verified at
    unblinding.
  - *Limitation, stated in advance:* blinding is partial. The template contains no
    model sentences, so stylistic differences can reveal which side is the LLM.
    Blinding removes label and order effects, not that one.
- **Hard rule (APPROVED).**
  - The held-out pass runs **exactly once**.
  - No prompt, check, lexicon or renderer change is made after any held-out output
    has been looked at.
  - If the hygiene criterion fails, that is recorded and the template ships as the
    Phase 4 generator. There is no retry.
  - API failures during the held-out pass count as failures. They are not re-run,
    unless the user decides otherwise before the results are looked at, as in
    Phase 3.
- **Model:** `gpt-6.1-sol` at effort `low`, configurable. The comparison with cheaper
  models is deferred to Phase 5 as a pre-registered comparison.
- **Spend cap (APPROVED): $5 for all Phase 4 API calls.** Spend is tracked from
  response usage at $2 / $10 per million input / output tokens.
  - The held-out pass (39 calls, ≤ $1.95 at the $0.05 planning price) is reserved
    first, before dev starts.
  - Dev iteration stops when spend plus that reserve would exceed the cap.
  - Planned worst case: smoke test $0.05, plus dev ≤ 52 calls ($2.60), plus
    held-out $1.95, for ≈ $4.60.
  - Adapter, template baseline, checks and replay cost $0.
- **Known limitation: upstream errors are reproduced faithfully (invariant 26).**
  D32#2 (dev) has gold risk tier `unresolved`. The determination is
  `not_applicable/conditions_not_met`, because the matcher returned `does_not_match`
  on Annex III 1(c). The answer must say "does not apply". Rendering it as uncertain
  would be a generator defect. A regression test pins this. An answer is only as
  correct as the Phase 3 determination behind it, and Phase 3 acceptance was NOT MET
  (above).

**End-to-end:**
- Obligation correctness (right obligations, right roles, right union/dedup per §4.1)
- Citation correctness (right `versioned_provision_id` per claim)
- Evidence traceability (every generated claim resolves to a real `EvidenceItem`, §16)

Extraction errors are evaluated separately from downstream errors, since an extraction
mistake silently poisons every later stage — an end-to-end failure must be attributable
to a specific stage, not just logged as "wrong answer."

---

## 19. The Invariants (Updated Post-Corpus-Validation)

1. **Canonical legal unit:** the smallest independently interpretable legal provision,
   per the source's own hierarchy — not a fixed grammatical or token unit.
2. **Technical retrieval unit:** may be a smaller embedding sub-chunk where a canonical
   provision is too large; always resolves back to its parent for generation.
3. **Parent resolution:** generation receives the complete canonical provision, never an
   isolated sub-chunk. **[Validated against real nested-exception case, Article
   5(1)(h).]**
4. **Cross-references:** preserved as graph relationships, resolved when structurally
   required — not expanded into chunks, not automatically traversed as siblings.
5. **Identity:** `logical_provision_id` + `versioned_provision_id` + `content_id` — three
   distinct identities, not two.
6. **Applicability:** a separate `ApplicabilityRule` layer, evaluated against case facts
   via a reusable rule-matching engine, never inferred from text existing.
7. **Temporal validity:** a hard eligibility constraint applied pre-retrieval, never a
   relevance score.
8. **Temporal query:** current and historical queries use the same retrieval mechanism,
   parameterized by `relevant_date` — no separate code paths.
9. **Failure handling:** `applicable / not_applicable / unresolved / not_in_force`;
   applicability is never inferred merely from text validity, and `not_in_force`
   (temporal fact about the provision) is never conflated with `not_applicable`
   (case-level exclusion conclusion) — see invariant 15.
10. **Version integrity:** `text_valid_from/to` ranges for one `logical_provision_id`
    must not overlap; gaps must be explicit, never accidental.
11. **Embedding deduplication:** identical canonical text shares one embedding
    (`content_id`), computed once.
12. **Index documents remain version-specific** even when they share an embedding, so
    temporal/applicability metadata stays independently filterable per version.

**Updated (post-corpus-check) additions:**

13. **Structural path is a variable-depth ordered list**, not fixed
    chapter/section/article/paragraph/point fields — required by Article 5's 4-level
    nesting versus Article 16's 2-level flat structure.
14. **Annex III terminal-case handling:** where an area has no lettered subdivision
    (Area 2), the area itself is the atomic provision — not a synthetic single-item
    wrapper.

**Updated (specification-clarification pass) additions:**

15. **`not_in_force` and `not_applicable` are distinct states**, never produced
    interchangeably: `not_in_force` means no text version was valid at
    `relevant_date` (a fact about the provision); `not_applicable` means a matching
    `ApplicabilityRule` was found and evaluated, and the case does not satisfy it (a
    fact about the case). See §14.
16. **Actor roles are a list, not a single enum**, with an explicit distinction
    between `actor_roles: []` + `known` (evidence-backed conclusion of no covered
    role) and `actor_role_status: unresolved` (insufficient facts to determine any
    role). Simultaneous roles (e.g. provider+deployer) each get independently
    evaluated, evidence-backed obligation sets, unioned and deduplicated downstream.
    See §4.1.

**Updated (Phase 0 scope-clarification pass) addition:**

17. **`ApplicabilityRule` ingestion is validated at load time, not query time:**
    temporal fields (`applicable_from`/`applicable_to`), `conditions`/`scope`
    well-formedness, and each rule's `legal_provision_id` reference to an ingested
    `Provision` are all checked during Phase 0 ingestion. This validation is
    independent of, and additional to, `Provision` text-validity validation
    (invariant 10) — the two remain separately modeled and separately checked. See
    §12.1.

**Updated (Phase 1 retrieval) additions:**

18. **Date payload indexes precede the vector graph.** `valid_from`/`valid_to` are
    indexed before any point is inserted, so HNSW builds filter-aware links;
    without them filtered recall collapses under selective date filters (§13.2).
19. **An explicit provision-level citation is a lookup, not a relevance guess** —
    it is promoted ahead of fused ranking, but only among date-eligible versions
    (§7.4).
20. **No retrieval component is adopted on expectation.** Reranking, fusion
    variants and citation handling each enter as a measured eval arm first; the
    reranker stayed off because it lost (§7.4, §18).

**Updated (Phase 3 design) additions:**

21. **The only LLM step between extraction and generation is the provision matcher**
    (§4.2). It sees a closed, exhaustive candidate set, emits per-candidate verdicts
    with verbatim quotes, and code verifies each verdict before use. Verification can
    only downgrade a verdict, never upgrade it. How verdicts combine into a status is
    code (§9.1).
22. **Silence is not a negative.** `does_not_match` requires a case quote showing the
    use case is something else. Absence of evidence is `insufficient`. Exception: an
    exception the Act requires a party to positively establish, such as the Article
    6(3) derogation, is recorded as `not_established` rather than `unresolved`. The
    default applies, and the record says so (§9.1).

**Updated (Phase 4 design) additions:**

23. **The generator sees only determinations.** Its only input is the adapter's
    `GenerationInput` (§16.2). It never sees the user's description or free-text
    facts, any `case_quote`, unfiltered retrieval or candidate lists, or eval labels.
24. **The generator never changes an `applicability_status`.** The status and reason
    are copied by code into a code-rendered status line, and the output schema has no
    status field (§16.3).
25. **Generation cannot add a determination.** The answer covers exactly the input
    determinations. There is no obligation, provision or conclusion that the input
    did not contain, and no obligation is rendered without its citation.
26. **Upstream determinations are reproduced faithfully, even when wrong.** The
    generator does not correct them, so every end-to-end error stays attributable to
    the stage that made it (§18).

---

## 20. Rejected Alternatives

| Rejected | Reason |
|---|---|
| Fixed-token chunking (e.g. 512 tokens with overlap) | Legal text already has an author-imposed hierarchical structure designed to be the unit of meaning and citation; fixed-size chunking risks cutting a provision from the conditions that qualify it. |
| One chunk = one Article, uniformly | Some articles (Article 16) are naturally atomic at that level; others (Article 5) contain multiple independently citable provisions nested up to 4 levels deep. A uniform rule under- or over-splits depending on the article. |
| Expanding cross-references into the referencing chunk's text | Causes duplication, version-consistency risk, inflated embeddings, and ambiguous citation provenance. Resolved at retrieval time instead. |
| Post-retrieval temporal filtering (retrieve broadly, filter by date after) | Causes candidate starvation — the correct historical version can be excluded from top-K before the date filter is ever applied. |
| Temporal proximity as a ranking score | Would let a legally invalid version outrank a valid one; validity must be a hard gate, not a trade-off factor. |
| Content-level (not version-level) index documents | Would make version-specific temporal/applicability metadata unfilterable for versions sharing identical text. |
| First-class `Obligation` entity distinct from `Provision`, introduced speculatively | Not justified by Article 16 (already atomic per point) or by the Article 5(1)(h) bundled-exception case (canonical-provision boundary sufficed). Deferred until corpus evidence of frequent unsplittable multi-obligation provisions. |
| Returning sibling provisions automatically alongside a matched parent | Floods generation context with structurally-adjacent-but-not-necessarily-relevant text; siblings are only pulled in via an explicit cross-reference dependency. |
| Free-text-only extraction with no structured intermediate representation | Would make hybrid retrieval's metadata/temporal filtering impossible; structured fields are required as enrichment even though free text remains available to semantic/BM25 search. |
| Plain RRF hybrid with no citation handling (Phase 1) | Measured: fusion with a vector ranking that cannot read "26(7)" dilutes BM25's exact match (bare-citation Recall@5 0.865 vs BM25 0.962). Replaced by exact-citation priority after RRF (§7.4). |
| Cross-encoder reranking by default (Phase 1) | Measured: bge-reranker-base lowered hybrid Recall@5 (use case 0.946 → 0.875; bare citations 0.865 → 0.769). Kept as an eval arm; a larger reranker must win as an arm before adoption. |
| Embedding whole provisions regardless of length (Phase 1) | Article 26(10) exceeds bge-base's 512-token window; its closing sentences would be silently truncated out of the embedding. |
| Retrieval-score cutoff as the Annex III / Article 5 decision mechanism (Phase 3 design) | Similarity is not legal membership. An interview-scheduling chatbot scores as "recruitment". It cannot evaluate Article 5(1)(h)'s exceptions or the Article 6(3) conditions, cannot produce `unresolved`, and its threshold would be tuned on seen results. Kept as the baseline arm (§4.2, §18). |
| Fixed keyword or category mapping per Annex III item (Phase 3 design) | Keyword lists break on free-text paraphrase. An extracted "Annex III area" field would be a legal conclusion and fail the zero-tolerance leakage check (§3.1). |
| Top-k candidates for the matcher (Phase 3 design) | A retrieval miss would become a confident negative. The scopes are short closed lists, so exhaustive evaluation is affordable (§4.2). |
| Extending `Case` with Article 6(3) / 6(1) facts (Phase 3 design) | Reopens the closed Phase 2 contract (labels, field rules, target, leakage check), and "performs profiling" is itself a defined legal term. The matcher reads the description instead (§9.1). |
| Extraction step directly outputting legal conclusions (risk_tier, prohibited_practice, actor_roles, substantial_modification) | Leakage risk — the model would silently guess conclusions from surface pattern-matching before any retrieval or grounding occurs, defeating the point of a grounded, citable determination. |

---

## 21. Open Questions and Stretch Goals

**Resolved in Phase 1:**
- ~~Physical pre-filtering behavior of the chosen vector DB under realistic
  date-filter selectivity (§13.2).~~ **Answered for Qdrant 1.19.1 HNSW**: filtered
  recall holds (0.90–1.00 at 10%–0.1% selectivity) *only* with date payload indexes
  built before the graph; without them it collapses (0.007 at 1%). That requirement
  is now in §13.2. Embedded local mode (exact scan) and server HNSW are reported as
  separate results; the first proves logical correctness only. Residual caveat: the
  selectivity sweep used random vectors, so absolute production recall on a large
  real multi-version corpus is still to be measured once one exists.

**Open / requires empirical verification during implementation:**
- Whether physical vector storage is deduplicated across
  `IndexDocument`s sharing a `content_id` (§15) — not measured in Phase 1; V1 does
  not depend on it. Compute-time dedup *is* verified (104 encodings for 200 index
  documents across the real and versioned indexes).
- Whether `ApplicabilityRule.legal_provision_id` referencing `logical_provision_id`
  (rather than `versioned_provision_id`) holds cleanly once real phase-in-date data is
  ingested for high-risk obligations and Article 5 prohibited practices — flagged here
  as a resolved-by-default design choice that has not been stress-tested against the
  actual Article 113 application timeline.

**Stretch goals (explicitly not V1):**
- Multi-hop cross-reference traversal (resolving chains of references, not just one
  hop).
- `obligation_id` as a child entity of `Provision`, if corpus evidence justifies it.
- Splitting `relevant_date` into `event_date` / `assessment_date` / `deployment_date`.
- Multi-jurisdiction support (using the already-captured but currently unused
  `jurisdiction` field).
- Incorporating the pending Digital Omnibus amendment to Article 5 once it has a
  confirmed effective date — a live, real-world exercise of the text-versioning model
  rather than a synthetic one.

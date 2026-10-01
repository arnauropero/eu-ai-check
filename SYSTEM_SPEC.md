# SYSTEM_SPEC.md — EU AI Act Compliance Determination System

Status: V1 architecture locked. **Phase 0 (corpus ingestion and validation) is
implemented as of 2026-10-01**; Phase 1 (retrieval) is next. This document is the
technical source of truth for implementation in Claude Code. Where a decision rests on
assumption rather than verified source material, it is marked **[ASSUMPTION]**. Where a
decision has been checked against the actual EUR-Lex / AI Act Explorer text, it is marked
**[VERIFIED]**.

Phase 0 changed the architecture in exactly one place: **Article 6 was added to the V1
corpus** on corpus evidence (§9). Every other invariant, identity, and structural
decision in this document survived contact with the real Official Journal text unchanged.
The corpus is 85 canonical provisions and 76 `ApplicabilityRule` records, built from
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
        │  1. Risk-tier classification (Annex III matching)
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

Two determination-adjacent LLM steps exist (extraction, generation); everything between
them is retrieval and rule evaluation, not free LLM reasoning. This is a deliberate
design choice to keep the legally load-bearing part of the pipeline auditable.

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

### 3.3 Why the raw text is preserved separately

The extraction schema is not the retrieval query. The original free text remains
available to semantic/BM25 retrieval directly; structured fields provide query
enrichment and metadata/temporal filtering, not a replacement for the text. Structured
fields are necessarily lossier than the source description.

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
  - type: chapter | section | article | paragraph | point | sub_point | annex | annex_area | annex_item
    value: str
  # ordered list, e.g.:
  # [ {article, "5"}, {paragraph, "1"}, {point, "h"}, {sub_point, "iii"} ]
```

Citation rendering and the "full structural path" display (e.g. `EU AI Act → Chapter III
→ Section 3 → Article 26 → Paragraph 2`) are derived by walking this path, not by
reading fixed columns.

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
Query → Semantic + BM25 search → Matched sub-chunk(s) → Reranking
      → Parent resolution → Full canonical provision(s) → Generation context
```

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
layer; they conclude directly from retrieved provision content.

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
  "this system is not high-risk" from "that classification route was not yet in force
  on the relevant date", which is precisely the `not_applicable` vs `not_in_force`
  distinction §14 forbids collapsing.

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

**[ASSUMPTION — requires empirical verification against the chosen vector DB]**
Logical pre-filtering does not guarantee efficient *physical* pre-filtering. Many ANN
indexes (e.g. HNSW-based) were built for the unfiltered vector space; filtered search
under a highly selective metadata filter can still degrade recall depending on the
engine (Qdrant/Weaviate have specific pre-filtering support; behavior varies elsewhere).
This must be verified empirically against the selected vector store under realistic
filter selectivity — not assumed from documentation.

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
  gap, not a rule-coverage gap, since there is no rule set to have gaps in).
- `applicable` / `not_applicable` — a direct conclusion drawn from matching retrieved
  provision content against case facts.

The four-way output contract (§16) is uniform across all determination types; only the
mechanism producing it differs depending on whether the determination is
`ApplicabilityRule`-backed.

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
  explanation: str
  supporting_evidence: [EvidenceItem]
```

`Obligation` (per-obligation output) is one instantiation of `Determination` with
`determination_type = obligation_applicability`; risk-tier, prohibited-practice, and
actor-role outputs use the same shape rather than a bespoke structure each.

**Value mapping for `risk_tier` and `prohibited_practice`.** Both determination types
reuse the generic `applicability_status` field rather than a separate tier enum:

- `determination_type = risk_tier`: `applicable` means the system matches an Annex III
  high-risk category (`high_risk`); `not_applicable` means it does not
  (`not_high_risk`); `unresolved` and `not_in_force` retain their standard §14/§14.1
  meanings, applied to Annex III category matching.
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

---

## 17. Retrieval Architecture Summary (Hybrid)

```
Query + relevant_date + case facts
        │
        ▼
Temporal eligibility filter (hard constraint, pre-retrieval)
        │
   ┌────┴────┐
   ▼         ▼
Semantic    BM25
   │         │
   └────┬────┘
        ▼
   Candidate sub-chunks → Reranking → Matched sub-chunk(s)
        │
        ▼
   Parent resolution → Full canonical provision(s)
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

**Extraction:**
- Field-level accuracy (semantic match for free-text fields: purpose, functionality)
- Exact-match for deterministic fields (dates, boolean actor-fact fields)
- **Zero-tolerance metric, not a threshold:** extraction must never populate an
  excluded/conclusion-level field (risk_tier, prohibited_practice, actor_roles,
  substantial_modification) — any occurrence in the eval set is a hard failure, not a
  score to optimize.

**Determination:**
- Correctness against the four-state contract (§14): `applicable` /
  `not_applicable` / `unresolved` / `not_in_force`, scored per state, not just overall
  accuracy — a system that's accurate on `applicable` cases but wrong on
  `not_in_force` vs. `not_applicable` discrimination has a real defect that an
  aggregate score would hide.
- **Zero-tolerance metric:** no `unresolved` case in the eval set may be silently
  produced as `not_applicable` or `applicable`. No `not_in_force` case may be produced
  as `not_applicable`. Both are hard failures (§14's invariant), not threshold misses.

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
| Extraction step directly outputting legal conclusions (risk_tier, prohibited_practice, actor_roles, substantial_modification) | Leakage risk — the model would silently guess conclusions from surface pattern-matching before any retrieval or grounding occurs, defeating the point of a grounded, citable determination. |

---

## 21. Open Questions and Stretch Goals

**Open / requires empirical verification during implementation:**
- Physical pre-filtering behavior of the chosen vector DB under realistic date-filter
  selectivity (§13.2) — must be benchmarked, not assumed from documentation.
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

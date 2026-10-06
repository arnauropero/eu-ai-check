# Phase 4 — Generation and citation: design note

Status: **DRAFT FOR APPROVAL** (2026-10-06, branch `phase-4/generation`).
Nothing here is implemented. No API call has been made for Phase 4.
Items marked **[PROPOSAL]** need the user's approval before any code is written.

Grounded in:
- SYSTEM_SPEC §14 (four-state join), §16 (output contract) and §18 (evaluation, including
  the Phase 3 NOT MET verdict and the post-hoc trail);
- PROJECT_PLAN Phase 4 and §9;
- the real Phase 3 code (`src/eu_ai_check/determination/`) and the cached records in
  `data/eval/results/*.runs.jsonl`.

---

## 0. Discrepancies between SYSTEM_SPEC §16 and the real Phase 3 output

Flagged here, not adapted silently. §16 describes a `Determination` that **Phase 3 does
not produce**, and the cached records hold less than §16 describes.

| # | SYSTEM_SPEC §16 says | Phase 3 code / cached records actually have |
|---|---|---|
| D1 | `EvidenceItem {logical_provision_id, versioned_provision_id, provision_citation, excerpt}` | **There is no `EvidenceItem` type anywhere in `src/`.** Evidence exists only in scattered pieces: `ProvisionStatus.versioned_provision_id` (obligations), `RoleResult.evidence` (logical ids only), matcher `Verdict`s (`provision_quote`, `case_quote`, `rationale`). |
| D2 | One `Determination` per type, with a `determination_type` field | One composite `pipeline.Determination` dataclass: `risk_tier: RiskTier`, `prohibited: Prohibition`, `roles: RoleResult \| None`, `obligations: dict[group, list[ProvisionStatus]]`, `match`. No `determination_type`. |
| D3 | `applicability_status` + separate `not_applicable_reason` | One compound string: `"not_applicable/conditions_not_met"`, `"not_applicable/not_yet_applicable"` (`logic.py`). |
| D4 | `explanation: str` on every Determination | **No explanation field exists.** The only prose is the matcher's per-candidate `rationale` (1–2 LLM sentences). Rule-based outcomes (roles, obligations) have no explanation at all. |
| D5 | Actor role uses the four-state contract | `RoleResult.status` is `known` / `unresolved` (and `not_in_force` in `role_label()` when roles are `None`). It is not `applicable` / `not_applicable`; per-role values are three-valued (`True`/`False`/`None`). |
| D6 | Evidence carries `versioned_provision_id` | The cached `*.runs.jsonl` records keep **no** `versioned_provision_id`, **no** excerpt, and only the **article-level group status** for obligations (`{"aiact:art_16": "applicable"}`). Per-provision `ProvisionStatus` and `rule_outcomes` are dropped by `Run.record()`. |
| D7 | Excerpt = canonical provision text | Not stored. Recoverable deterministically from `data/corpus/provisions.json` (all referenced ids exist there, including `aiact:art_3:point_3/4/23` and `aiact:art_25:*`). |
| D8 | (not addressed) | Matcher evidence includes `case_quote`: a **verbatim span of the user's description**. Code checked that it is verbatim, but it is still user text (see open question Q2). |

**Consequence.** The cached records cannot be fed to a generator as they are. Phase 4
needs a deterministic **adapter** that produces §16-shaped objects. It can do that at
zero API cost: `scripts/retry_determination_failed.py` already shows that recorded
verdicts replay through `Pipeline` exactly (a `Replay` matcher, with an exact-match
check against the original record). The replay rebuilds the full in-memory
`Determination`, including per-provision `ProvisionStatus`, `versioned_provision_id`
and `rule_outcomes`. The adapter then maps it onto §16.

**[PROPOSAL] Adapter (`generation/adapter.py`, deterministic, no LLM):**

```yaml
GenerationInput:                 # everything the generator ever sees
  case_ref: str                  # e.g. "D10#0"; opaque to the model
  relevant_date: date
  determinations: [Determination]   # §16 shape, one per item below

Determination:                   # §16, plus two fields marked NEW
  determination_id: str          # NEW: stable ref, e.g. "risk_tier", "obligation:aiact:art_26:para_2"
  determination_type: risk_tier | prohibited_practice | actor_role | obligation_applicability
  applicability_status: applicable | not_applicable | unresolved | not_in_force
  not_applicable_reason: conditions_not_met | not_yet_applicable | null   # split from D3
  explanation: str               # built by code from structured facts (D4), never by an LLM
  supporting_evidence: [EvidenceItem]
  unresolved_inputs: [MissingInput]  # NEW: non-empty iff status is unresolved (§3 below)
  # risk_tier only (§16): system_category, derogation

EvidenceItem:                    # §16 exactly, plus an id
  evidence_id: str               # NEW: "E1", "E2", ... unique within one GenerationInput
  logical_provision_id: str
  versioned_provision_id: str
  provision_citation: str        # Provision.citation() — rendered from the path, §6
  excerpt: str                   # full canonical text of that version, from the corpus

MissingInput:
  kind: matcher_question | case_fact | upstream_determination
  ref: str                       # provision id, fact name, or determination_id
  question: str                  # code-owned text: the scope question (scopes.py), the fact's
                                 # field description, or "depends on <determination>"
```

Mapping decisions the adapter needs (each **[PROPOSAL]**):
- *Obligations:* one `Determination` per obligation provision (12 under Article 16, 12
  under Article 26), not one per article. The per-article status (`group_status`)
  is kept as a summary only. `mixed` never occurred in the 123 cached M-low records,
  but if it ever does the adapter raises an error rather than choosing a status.
- *Actor role (D5):* one `Determination` per role (`actor_role:provider`,
  `actor_role:deployer`). `True` → `applicable`, `False` → `not_applicable /
  conditions_not_met`, `None` → `unresolved`, `roles is None` → `not_in_force`. This is
  a mapping onto §16, so it needs a SYSTEM_SPEC edit (§7 below).
- *Evidence per determination:* risk tier and prohibited practice cite the provisions
  whose verdict is `matches`, plus the route provision (Article 6(1)/6(2)) and, where
  assessed, the 6(3) provisions. Roles cite `RoleResult.evidence`. Obligations cite the
  provision itself. The excerpt is the whole canonical text of the cited version, so
  the excerpt check (§5, C3) has a precise reference.
- *explanation (D4):* built by code from structured state, e.g. "Annex III route:
  not established (Annex III 4(b) insufficient); Article 6(1) route: not met". It is
  data for the generator, not user-facing prose.

---

## 1. Input contract

**[PROPOSAL] Invariant G1 — the generator sees only determinations.** Its only input
is a `GenerationInput` (above): already-computed `Determination` objects with their
`EvidenceItem`s and `unresolved_inputs`. It never receives:
- the raw user description, or `CaseFacts` free-text fields;
- unfiltered retrieval results, candidate lists, or `does_not_match` verdicts that
  no determination cites;
- gold labels, `expected` blocks, or any other eval metadata.

**Invariant G2 — the generator never changes an `applicability_status`.** Status and
`not_applicable_reason` are copied by code from the input into the output. The model
has no field through which it could set them (§2), and check C4 (§5) verifies that its
wording does not contradict them.

**Invariant G3 — generation cannot add a determination.** The output covers exactly
the input `determination_id`s: no new obligation, provision, or determination.

G1 is enforced by type: the generator's function signature accepts a
`GenerationInput` and nothing else. A test checks that the serialised prompt contains
no field outside that schema.

---

## 2. Output design: A vs B

### Option A — free-text answer with inline citations

The model writes the whole answer as prose with markers such as `[E3]`.

- *Traceability check:* parse the markers, then check that each one resolves to an
  input `evidence_id`. That proves only that the markers are valid. It does not prove
  that a sentence is *supported* by its marker. Deciding what counts as "a claim"
  needs sentence segmentation, so uncited sentences can be found only heuristically.
- *"No obligation without a citation":* this needs a detector for obligation
  language ("must", "shall", "is required to", article references) per sentence, plus
  a check that each such sentence carries a marker. That works, but it depends on
  wording, and false negatives are silent.
- *Status preservation:* only lexically, by scanning the prose for "does not apply",
  "not applicable" and the like near each article mention. This is brittle.
- *Rigidity cost:* none. The wording is the most natural and fluent.

### Option B — structured claims first, prose rendered from them

The model returns strict JSON (schema-enforced, the same mechanism as the Phase 3
matcher):

```yaml
GeneratedAnswer:
  claims:
    - claim_id: str
      determination_id: str        # must be an input determination_id
      text: str                    # one or two sentences, no status words required
      evidence_ids: [str]          # >= 1, each from that determination's supporting_evidence
      quote: str | null            # optional verbatim span from one cited excerpt
      quote_evidence_id: str | null
```

Code then renders the answer. For each determination, in a fixed order (risk tier →
prohibited practice → roles → obligations), code writes a **status line** from the
input status: "Article 26(2): applies", "Article 16(a): not yet applicable (applies
from 2026-08-02)", "Article 16(a): no version of this provision was in force on
2024-07-01". The model's claims for that determination follow, each with its
citations rendered by code from the `EvidenceItem` (`provision_citation`,
`versioned_provision_id`).

- *Traceability check:* every claim is a record with `determination_id` and
  `evidence_ids`, so traceability is set membership. It is exact, with no parsing.
- *"No obligation without a citation":* obligations appear only as code-rendered
  status lines, one per obligation determination, and code attaches that provision's
  own `EvidenceItem` to each. An uncited obligation cannot be rendered. Claim text is
  still scanned for article references that it does not cite (C5).
- *Status preservation:* by construction, because the status line is code-owned and
  the schema has no status field. C4 adds a lexical check on the claim text.
- *Rigidity cost:* real but bounded. The answer reads as sections ("status, then two
  to four supporting sentences"), not as an essay. Cross-determination narrative
  ("because the system is high-risk, Article 16 applies") must be split into one
  claim per determination, or written as a claim on one determination that cites the
  other's evidence. The second form is disallowed by C2, so the link has to be stated
  in code-rendered text instead (see below).

### Recommendation: **B**, with code-owned status lines **[PROPOSAL]**

The Phase 4 acceptance criterion is a traceability property. B makes it a structural
property that can be checked exactly. A can only approximate it. The repo has already
made the same choice twice: the extraction schema (Phase 2) and the matcher's
verdict-plus-quote design (Phase 3) both move the guarantee from wording into structure,
then verify with code. The cost is less fluent prose. For a compliance tool, a
sectioned answer is acceptable, arguably better. The links between determinations
(risk tier → roles → obligations) come from code: the adapter's `explanation` already
encodes them, and the renderer can print "depends on: risk tier (unresolved)" from
`unresolved_inputs` without any model text.

---

## 3. Behaviour per status

Status lines are code-rendered in every case. The model's claims add explanation and
can never contradict the line.

| Status | Code-rendered status line | What the claims must do | Forbidden |
|---|---|---|---|
| `applicable` | "<citation>: applies to this case." For obligations: the obligation, cited. | Say why, citing the determination's evidence (e.g. the matched Annex III item). Obligations may be paraphrased, with an optional verbatim `quote`. | Hedging that turns it into "may apply". |
| `not_applicable` / `conditions_not_met` | "<citation>: does not apply to this case." | Name the condition that fails (role, category), citing evidence. | Implying a permanent exemption beyond what the condition says. |
| `not_applicable` / `not_yet_applicable` | "<citation>: not yet applicable; applies from <applicable_from>." | May explain the phase-in. | Wording it as an exclusion or as "not in force". The provision text *is* in force. |
| `unresolved` | "<citation>: could not be determined from the information provided." | See below. | Any lean towards applies / does not apply; any wording of system failure. |
| `not_in_force` | "<citation>: no version of this provision was in force on <relevant_date>." | At most, state the date the text entered into force (from the corpus). | **"not applicable", "does not apply", "excluded", "exempt", "out of scope"** or any case-level conclusion (§14 invariant). |

**`unresolved` — what the answer must say.** It is a finding about the
*information*, not about the system. The rendered block has three parts:

1. The status line above. Fixed wording, never "error", "failed" or "unable to
   process".
2. **"To decide this, we would need to know:"** followed by one bullet per
   `MissingInput`, rendered by code from its `question` (e.g. "Is the AI system
   intended to be used as described in Annex III point 4(b)?", or for a role: "Does
   your organisation use the system?"). For an `upstream_determination`, the bullet
   reads "depends on <that determination>, which is itself unresolved". This list is
   code-generated, so it is complete and cannot be invented.
3. Optional model claims explaining *why* that information matters, each citing the
   provision concerned. For example, using the D10 rationale: whether flagged
   deviations are used to evaluate individual loan officers decides Annex III 4(b).

`unresolved_inputs` is built from data that already exists:
- risk tier and prohibited practice: the scopes whose combination is unknown,
  i.e. the `insufficient` verdicts that could change the outcome (§4.2/§9.1). The
  adapter re-runs the combination with each one set to true and to false, and lists
  only those whose flip changes the outcome. This is deterministic and cheap;
- roles: the `CaseFacts` fields that are `null` and feed that role's formula (`roles.py`);
- obligations: the rule conditions that evaluated to `None` (`rule_outcomes`) and the
  upstream determination behind them.

Open question Q3: should the matcher's `rationale` (LLM text that paraphrases the
user's description) be passed through? The table above assumes it may appear in claims
but never in the code-rendered "we would need to know" list.

---

## 4. Known limitation: upstream determinations can be wrong

**[PROPOSAL] Invariant G4 — faithful reproduction.** The generator reports the input
determination as it is, even when it is wrong. It has no access to the facts that
would let it notice (G1). If it disagrees with a status anyway, it has no field to
express that (G2, schema). The checks treat any contradiction as a failure.

The concrete case is **D32#2** in the official run
(`determination_v1_41c_retry.runs.jsonl`). The gold label is `unresolved`. The matcher
returned `does_not_match` on Annex III 1(c), so risk tier came out
`not_applicable/conditions_not_met`. The generated answer for that record must say "does
not apply". Rendering it as uncertain would be a generator defect, even though it
would happen to be closer to the truth. Errors are attributed to the stage that made
them (SYSTEM_SPEC §18, end-to-end), and a generator that "corrects" upstream output
breaks that attribution and hides Phase 3 defects.

Where to document:
- **SYSTEM_SPEC §18, new "Generation — known limitation" paragraph**: G4, D32#2 as
  the worked example, and the rule that Phase 4 metrics are measured against the
  *input* determination, never the gold label.
- **A regression test** (`tests/test_generation.py`): D32#2 used as a fixture; the
  rendered answer's risk-tier line must be the `not_applicable` line.
- **README Known limitations (Phase 5)**: one plain-language sentence. The answer is
  only as correct as the determination behind it, and Phase 3 did not meet its
  acceptance bar (prohibited `not_applicable` 0.632, and one zero-tolerance
  violation). This is a known limitation of the system and must not be read as a
  generator caveat.

---

## 5. Deterministic checks and acceptance criteria

All checks are code, with no LLM. They run on every generated answer. **A failing
answer is never shown:** it is replaced by the code-only rendering (status lines,
evidence citations, and the "we would need to know" lists, with no model claims).
That fallback is valid output by construction. It is also counted, so it can never
quietly become the default.

| ID | Check | Exact definition |
|---|---|---|
| C0 | Schema | The response parses against the strict `GeneratedAnswer` schema; refusal or incomplete response → fail. |
| C1 | Evidence exists | Every `evidence_ids` entry is an `evidence_id` present in the input. |
| C2 | Evidence belongs to the claim's determination | Every `evidence_id` is in that determination's `supporting_evidence`; `determination_id` exists in the input. |
| C3 | Excerpt fidelity | If `quote` is set, it is a verbatim substring (after the matcher's `_norm` whitespace normalisation) of the `excerpt` of `quote_evidence_id`, and that id is in the claim's `evidence_ids`. Separately, each rendered `excerpt` equals the corpus text of its `versioned_provision_id` (an adapter self-check). |
| C4 | Status preservation | (a) The rendered status for every determination equals the input status and reason. This holds by construction and is tested. (b) Claim text for a determination contains no status phrase from another state's lexicon: e.g. no "does not apply" / "not applicable" / "exempt" on an `applicable`, `unresolved` or `not_in_force` determination; no "applies to you" / "you must" on `not_applicable`, `unresolved` or `not_in_force`. |
| C5 | No obligation without a citation | (a) Every obligation determination is rendered with its own `EvidenceItem`. This holds by construction and is tested. (b) No claim text contains a provision reference (regex over "Article N(…)", "Annex III point …", "Art. N") that does not resolve to a `provision_citation` among that claim's own `evidence_ids`. (c) No claim text uses obligation language ("must", "shall", "is required to", "has to") unless it cites at least one obligation provision. |
| C6 | Coverage | Every input `determination_id` is rendered exactly once; no claim references an id outside the input (G3). |
| C7 | Unresolved completeness | Every `unresolved` determination renders a non-empty "we would need to know" list equal to its `unresolved_inputs`. This holds by construction and is tested. |
| C8 | `not_in_force` wording | No claim on a `not_in_force` determination contains the `not_applicable` lexicon (a stricter, separate C4(b) with its own counter, because it is the §14 invariant). |

C4(b), C5(b–c) and C8 are lexical. They can miss a paraphrase, so a pass does not prove
faithfulness. That is why a small human-read sample is proposed below (reported, not
gated).

### [PROPOSAL] Acceptance criteria (replacing PROJECT_PLAN's Phase 4 line)

Pre-registered: fixed and committed **before** the first generation API call,
following Phase 3's discipline. The input sets are defined in §6.

1. **Shipped output, zero tolerance.** On every input in the dev and held-out sets,
   the shipped answer (generated, or fallback) passes C0–C8. Target: **1.00**. Any
   failure is a code bug.
2. **Raw generator pass rate.** The share of answers passing C0–C8 on the first
   attempt, *before* fallback. Target: **≥ 0.95** on the held-out set. Reported
   per check and per status.
3. **Hard invariants on raw output, zero tolerance:** C4 and C8 violations on the
   held-out set: **0**. A model that tries to change a status has to be caught, and
   should not be happening at all.
4. **Fallback rate ≤ 0.05** on the held-out set. This is the same quantity as
   criterion 2, but it is stated separately so that a degenerate generator which
   always falls back cannot pass.
5. **Faithful reproduction:** on the inputs where the determination differs from
   gold (in the official run: D32#2, D10/D26/D28 risk tier, and the 32 prohibited
   `not_applicable`→`unresolved` misses), the rendered status equals the
   *input* status in **100%** of cases (G4).
6. **Reported, not gated:** a human read of 20 sampled answers (stratified by
   status, chosen by seeded random before reading), each marked faithful / unfaithful
   to its cited excerpt. This covers what the lexical checks cannot. It becomes a
   gate only if the user wants one; proposed threshold if so: ≥ 19/20.

The PROJECT_PLAN Phase 4 acceptance line would read:

> Acceptance (pre-registered, SYSTEM_SPEC §18): every shipped answer passes the
> deterministic checks C0–C8 (1.00); raw generator first-attempt pass rate ≥ 0.95
> and fallback ≤ 0.05 on the held-out input set; zero status-changing (C4) and zero
> `not_in_force`-as-`not_applicable` (C8) violations; input status reproduced
> faithfully in 100% of known-wrong upstream determinations.

---

## 6. Development approach

- **No pipeline re-run.** Inputs come from replaying cached verdicts through
  `Pipeline` (as `retry_determination_failed.py` does) and then the adapter. Zero API
  cost, and exact replay is asserted.
- **[PROPOSAL] Input sets.**
  - *Dev (iterate freely):* the official run, `determination_v1_41c_retry.runs.jsonl`,
    M-low, 123 records (41 cases × 3).
  - *Held-out (one pass, pre-registered):* `dev_social_scoring_v1_41c.runs.jsonl`, M-low,
    123 records. These are the same 41 descriptions but different determinations
    (prohibited `not_applicable` 0.931 vs 0.632; D32 risk tier wrong 3/3). Weakness:
    descriptions overlap with dev, so this tests status mixes, not unseen use cases.
    Alternative: build new held-out determinations by running the deterministic
    baseline B on `determination_heldout_5_1_c.json`, also at zero cost. But B never
    produces `unresolved`, so it is a poor test for §3. User decision: Q4.
  - The 3 `not_in_force` records (D38) appear in both sets.
- **Configurable model.** `--generation-model` and `--generation-effort`, with no
  default model hard-coded in the generator. A provider interface like `Matcher`, plus
  a fake generator for tests. **Choosing between Sol and cheaper models is deferred to
  Phase 5** as a pre-registered comparison over these same checks. Phase 4 is
  developed and accepted on one model, which the user picks (Q1).
- **Tests first, at zero cost:** the adapter, the renderer and C0–C8 are all
  deterministic, and are tested against hand-written `GeneratedAnswer` fixtures,
  including adversarial ones (an id that is not in the input, a borrowed evidence id,
  a non-verbatim quote, "does not apply" on a `not_in_force` determination).
- **Do not touch:** the Phase 3 prompts, the SHA pins, the cached results, and
  `DEFAULT_PROMPT_VARIANT = "baseline"`.

### Rough cost estimate

Anchor: the Phase 3 matcher on `gpt-6.1-sol` at effort `low` cost $6.86 for 123 calls,
about **$0.056 per call**, at a mean of 8.2k input and 4.1k output tokens (output
includes reasoning).

A generator call, estimated: 3–6k input tokens (around 30 determinations; obligation
excerpts are short, about 30–80 tokens each) and 1.5–3k output tokens. That is about
0.4–0.6 of a matcher call, so **≈ $0.02–0.035 per call** on Sol at effort `low`.

| Activity | Calls | Est. cost (Sol, low) |
|---|---|---|
| Smoke test (made-up input) | 1–3 | < $0.10 |
| Dev iteration, 41-case slice (1 repeat each), ~8 rounds | ~330 | $7–12 |
| Pre-registered final run, dev + held-out | 246 | $5–9 |
| **Phase 4 total** | | **≈ $12–21** |

A cheaper model would cut this roughly in proportion to its price. Those numbers
belong to the Phase 5 comparison, not to Phase 4.

---

## 7. Proposed edits to SYSTEM_SPEC and PROJECT_PLAN (not made yet)

**SYSTEM_SPEC**
1. §16: state that the real Phase 3 output is the composite `pipeline.Determination`,
   and that the §16 shape is produced by the Phase 4 adapter (D1–D7). Add
   `determination_id`, `evidence_id` and `unresolved_inputs` / `MissingInput`. Record
   the compound-string ↔ status + reason split (D3).
2. §16: add the actor-role mapping onto the four states (D5).
3. §16 or new §16.1: Generation invariants G1–G4 and the `GeneratedAnswer` schema.
4. §18: a new "Generation" subsection with the checks C0–C8, the pre-registered
   targets, the input sets, and the known-limitation paragraph (§4 above).
5. §19 Invariants: add G1–G4.

**PROJECT_PLAN**
1. Phase 4 acceptance: replace it with the text in §5.
2. Phase 4 deliverables: the adapter, the renderer, the checks, and the
   `eu-ai-check eval-generation` harness.
3. Phase 5: add the pre-registered generation-model comparison (Sol vs cheaper),
   using the C0–C8 metrics.

---

## 8. Open questions for the user

- **Q1.** Which model and effort should Phase 4 be developed and accepted on? The
  suggestion is `gpt-6.1-sol` at `low`, for continuity with Phase 2/3.
- **Q2.** Matcher `case_quote`s are verbatim spans of the user's description (D8).
  Should they count as "raw user text" under G1 and be excluded? Or should they be
  allowed, since they are code-verified spans that support a cited verdict? The
  proposal is to exclude them: G1 stays absolute, and the answer is addressed to the
  user, who already knows what they wrote.
- **Q3.** Matcher `rationale`s: should they pass through to the generator as part of
  `explanation`? They are LLM text and can paraphrase the user's description. The
  proposal is to include them only for `insufficient` verdicts that appear in
  `unresolved_inputs`, because that is where they carry the "what is missing" signal.
- **Q4.** Held-out set: v1 dev-run records (same descriptions, different
  determinations), or baseline-B determinations on the 5(1)(c) held-out cases (new
  descriptions, no `unresolved`), or both?
- **Q5.** Should the human-read faithfulness sample (criterion 6) be a gate, or only
  reported?
- **Q6.** Granularity of obligations: one rendered line per provision (24 lines when
  both articles apply), or one per article with its provisions listed under it? The
  adapter keeps one determination per provision either way. This only affects the
  renderer.

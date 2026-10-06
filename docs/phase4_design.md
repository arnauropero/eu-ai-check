# Phase 4 — Generation and citation: design note

Status: **design approved in part; round 2 awaiting approval** (2026-10-06, branch
`phase-4/generation`). No generator code exists, and no Phase 4 API call has been made.
The decisions taken so far are listed in §8. Items still marked **PROPOSED** (the
decision rule, the targets and the spend cap) need approval. Earlier **[PROPOSAL]**
tags in §0–§4 were approved unless §8 says otherwise.

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
| D8 | (not addressed) | Matcher evidence includes `case_quote`: a **verbatim span of the user's description**. Code checked that it is verbatim, but it is still user text (decided: excluded, §8). |

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
  determination_id: str          # NEW: stable ref, e.g. "risk_tier", "obligation:aiact:art_26"
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
- *Obligations (**decided: per article**):* one `Determination` per article
  (`obligation:aiact:art_16`, `obligation:aiact:art_26`), carrying the article's
  `group_status`. The cached data holds only that per-article status, so the output
  must not suggest finer granularity. The article's provisions are its evidence.
  Their excerpts are included only when the article is `applicable` or
  `not_yet_applicable`. `mixed` never occurred in the 123 cached M-low records, but
  if it ever does the adapter raises an error rather than choosing a status.
  (Superseded: one determination per provision.)
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

Decided (Q3): the matcher's `rationale` is passed through only for the `insufficient`
verdicts listed in `unresolved_inputs`. It may inform claims. It never appears in the
code-rendered "we would need to know" list.

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

### Acceptance criteria and the template baseline (round 2; PROPOSED, see SYSTEM_SPEC §18)

A **template renderer** (no LLM, $0) is the Phase 4 baseline. It uses the same
adapter, renderer and checks, with no model claims. It therefore passes C0–C8 *by
construction*, and check pass rates cannot tell it apart from the LLM. The LLM's
added value has to be measured separately, as follows.

**Acceptance (both arms):**
1. Every shipped answer passes C0–C8: **1.00**.
2. Faithful reproduction: on the held-out records whose determination differs from
   gold (D02, D05, D28, D35, D36: 15 records), the rendered status equals the input
   status in **100%** of them. D32#2 is pinned separately by a regression test (it
   is in dev).
3. Reported, not gated (decided): a human faithfulness read of 20 sampled answers.

**PROPOSED decision rule: adopt the LLM generator over the template only if all of
the following hold on the held-out set.**
1. *Hygiene:*
   - raw first-attempt pass rate ≥ 0.95;
   - fallback rate ≤ 0.05;
   - zero C4 violations;
   - zero C8 violations.
2. ***Grounded-explanation coverage ≥ 0.90*** (the improvement metric, deterministic).
   Count the explainable determinations: risk tier and prohibited practice when
   `applicable` or `not_applicable`, obligation articles when `applicable` or
   `not_yet_applicable`, and every `unresolved` determination. Coverage is the share of
   them that carry at least one model claim passing C0–C8. The template scores 0.00 by
   construction. This metric shows that the LLM adds *verified* content. It does not
   show that the content is better. Hence 3.
3. ***Blind pairwise preference ≥ 15 / 20*** (the improvement metric, quality). The user
   rates 20 held-out pairs (template vs LLM, same input), sampled with a fixed seed and
   stratified by input state, with order randomised and the arm hidden. Ties count for
   the template. This can run in the same sitting as the faithfulness read.

If the rule fails, the **template ships** as the Phase 4 generator. Phase 4
acceptance (1–2) is then still met, because traceability is the acceptance property
and the template satisfies it. The cost of the LLM is justified only by 2 and 3.

Note on 3: you decided the faithfulness read is reported, not gated. Criterion 3 is a
different human judgement, and it gates *adoption of the LLM*, not Phase 4
acceptance. If you want no human gate at all, drop 3 and raise 2 to ≥ 0.95. The cost
is that adoption would then rest on a metric that only measures presence, not
quality.

---

## 6. Development approach

- **No pipeline re-run.** Inputs come from replaying cached verdicts through
  `Pipeline` and then the adapter. `scripts/verify_generation_inputs.py` confirms the
  replay is exact for all 123 M-low records.
- **Input derivability (verified, item 5).** For every cited provision of every
  record, `versioned_provision_id` and the excerpt follow from the corpus plus
  `relevant_date`. Each of the 110 provisions has exactly one version (valid from
  2024-08-01, open-ended): 4,295 lookups derived, and the pipeline's own
  `ProvisionStatus.versioned_provision_id` agrees in every case. **Not derivable:**
  D38#0, D38#1 and D38#2, 24 lookups each (every Article 16 and 26 provision).
  `relevant_date` is 2024-06-15, before any text was in force. This is correct
  `not_in_force` behaviour, not a data gap. Those determinations carry no
  `EvidenceItem` (SYSTEM_SPEC §16.2).
- **Split (decided, frozen in `a105e66`, before any generator prompt).**
  `data/eval/generation_split.json`, produced by `scripts/make_generation_split.py`
  and SHA-pinned in `tests/test_generation_split.py`. It is split by case id,
  stratified on gold risk tier, with seed 20261006. D38 is forced into held-out
  because it is the only `not_in_force` case.

  | Side | Cases | Records | Input determinations |
  |---|---|---|---|
  | dev | D01 D03 D04 D07 D08 D09 D10 D11 D12 D14 D16 D17 D18 D19 D21 D23 D24 D25 D26 D29 D30 D32 D33 D34 D37 D39 D40 D41 (28) | 84 | 114 applicable, 154 not_applicable, 80 unresolved, 0 not_in_force |
  | held-out | D02 D05 D06 D13 D15 D20 D22 D27 D28 D31 D35 D36 D38 (13) | 39 | 60 applicable, 45 not_applicable, 42 unresolved, 15 not_in_force |
  | extra held-out | H01–H08 | none yet | **no cached determinations** (see below) |

  Dev has no `not_in_force`. That state is covered by deterministic tests on D38-shaped
  fixtures, and by held-out.
- **H01–H08 have no determinations (needs your decision).** The 5(1)(c) held-out set
  was never run, so there is nothing cached to generate from. The options are:
  - **(a) M-low, baseline prompt, 1 repeat:** 8 matcher calls, ≈ $0.46. This uses up
    the set's status as an *untouched* confirmation set for any future 5(1)(c)
    matcher change (SYSTEM_SPEC §18 kept it for that purpose).
  - **(b) Score-cutoff baseline B:** $0, local embedder only. New descriptions, and
    the H set stays untouched for the matcher. But B cannot produce `unresolved`, so
    H07/H08 come out wrong. That is still a valid faithful-reproduction test, since
    the generator must render B's status as given.
  - **(c) Drop H from Phase 4.**

  **Recommendation: (b).** It keeps the H set's matcher value, costs nothing, and
  held-out already has 42 `unresolved` determinations from the D cases.
- **Model:** `gpt-6.1-sol`, effort `low` (decided). It is configurable, with a fake
  generator for tests. The cheaper-model comparison happens in Phase 5.
- **Tests first, at zero cost:** the adapter, both renderers and C0–C8 are tested on
  hand-written `GeneratedAnswer` fixtures, including adversarial ones.
- **Do not touch:** the Phase 3 prompts, the SHA pins, the cached results, and
  `DEFAULT_PROMPT_VARIANT = "baseline"`.

### Cost breakdown (corrected)

**Why the first estimate said $12–21.** It was not $0.02–0.035 × 41. It assumed
about 574 calls: 8 dev rounds × 41 cases (328), plus a final pass over 246 records
(123 dev + 123 `social-scoring-v1` held-out). That is 574 × $0.02–0.035. You have
since excluded that held-out set and capped the spend. The per-call figure was also
too low, as the measurements below show.

**Per call, measured inputs.** Prices are from `cli.py`: Sol costs $2 / $10 per
million input / output tokens. That reproduces the matcher's $0.057 per call
(8.2k in, 4.1k out).
- *Input:* cited excerpts measured by `verify_generation_inputs.py`, with obligation
  text only for applicable / not-yet articles. Median 10.0k chars (≈ 2.5k tokens),
  max 29.5k chars (≈ 7.4k tokens). Adding ≈ 1.5k tokens for instructions, schema and
  determination metadata gives **≈ 4k median, 9k max → $0.008–0.018**.
- *Output:* about 6 determinations (tier, prohibited, 2 roles, 2 articles) × 2–3
  claims × ≈ 60 tokens ≈ 1k visible, plus 1–2k reasoning at `low`. That is
  **≈ 2–3k tokens → $0.02–0.03**.
- **Total ≈ $0.03 typical, $0.05 upper. The plan below uses $0.05.** The smoke test
  measures the real figure.

**Plan under the PROPOSED $5 cap** (1 generation per record; Phase 4 never re-runs
the matcher):

| Item | Calls per pass | Passes | Calls | At $0.05 | At $0.03 |
|---|---|---|---|---|---|
| Smoke test (made-up input) | 1 | 1 | 1 | $0.05 | $0.03 |
| Dev iteration: repeat 0 of each dev case | 28 | ≤ 1.9 (budget-limited) | ≤ 52 | ≤ $2.60 | ≤ $1.56 |
| Held-out, pre-registered, all 3 repeats | 39 | 1 | 39 | $1.95 | $1.17 |
| H01–H08 generator (option b) | 8 | 1 | 8 | $0.40 | $0.24 |
| **Total** | | | **≤ 100** | **≤ $5.00** | **≤ $3.00** |

- *Repeats:* dev uses repeat 0 only, because repeats mostly share a determination.
  Held-out uses all 3 repeats, because the records differ (matcher variance), which
  also partly exposes generator variance.
- *Reserve first:* the held-out and H passes ($2.35 at the planning price) are
  reserved before dev starts. Dev iteration stops when spend + reserve reaches $5.
  The harness keeps a spend ledger from response usage.
- With option (a) for H, add 8 matcher calls (≈ $0.46). Dev then drops to about 43
  calls.
- $0: the adapter, the template baseline, the checks, the split, the tests and all
  replay work.

---

## 7. Edits to SYSTEM_SPEC and PROJECT_PLAN

Applied in `34acd2a`, separately from this note:
- SYSTEM_SPEC §16 as-built note and §16.1 (what Phase 3 really produces);
- §16.2 (adapter input, per-article obligations);
- §16.3 (claims-first output, template renderer);
- §18 Generation block (split, C0–C8, baseline, PROPOSED rule / targets / cap, known
  limitation);
- §19 invariants 23–26 (= G1–G4);
- PROJECT_PLAN Phase 4 deliverables and PROPOSED acceptance, and the Phase 5 model
  comparison.

---

## 8. Decisions and open questions

**Decided (2026-10-06):**
- Option B.
- `gpt-6.1-sol` at `low`.
- No verbatim user quotes in the input.
- Matcher rationales only for unanswered verdicts.
- Obligations grouped per article.
- The faithfulness sample is reported, not gated.
- The split is by case id, with H01–H08 as extra held-out.
- The template baseline.

**Open:**
- **Q7.** H01–H08 determinations: (a) M-low, (b) baseline B (recommended), or (c) drop.
- **Q8.** Approve the decision rule: coverage ≥ 0.90 plus pairwise preference ≥ 15/20.
  Or no human gate, with coverage ≥ 0.95.
- **Q9.** Approve the $5 cap and the reserve-first spending plan.
- **Q10.** Once Q7–Q9 are answered, the PROPOSED items in SYSTEM_SPEC §18 and
  PROJECT_PLAN become the Phase 4 pre-registration, committed before the smoke test.

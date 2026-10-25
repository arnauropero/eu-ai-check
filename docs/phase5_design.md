# Phase 5 — End-to-end evaluation and polish: design note

Status: **DRAFT for review** (2026-10-25, branch `phase-5/end-to-end`). No Phase 5
code beyond the eval-set label tests exists, and no Phase 5 API call has been made.
Items marked **[DECIDED]** come from the user's decisions of 2026-10-25. Items marked
**[PROPOSAL]** need the user's approval. Once approved, the binding text moves to
SYSTEM_SPEC §18 in the pre-registration commit, as in Phases 3 and 4.

Grounded in:
- PROJECT_PLAN Phase 5 and §8 (including the Phase 3 deviation, `ce8e60d`);
- SYSTEM_SPEC §18, "End-to-end" (obligation, citation and evidence correctness, with
  per-stage attribution), and the Phase 2–4 pre-registrations;
- the as-built code: `extraction/openai_extractor.py`, `determination/pipeline.py`,
  `generation/{adapter,generator,checks,spend}.py`;
- measured costs from the frozen ledgers (§6).

---

## 1. What "end-to-end" runs

One case = one free-text description plus an assessment date. The runner chains the
shipped configuration of every stage, with nothing gold in between:

| Stage | Component | Frozen configuration | Calls |
|---|---|---|---|
| Extraction | `OpenAIExtractor` | `gpt-6.1-sol`, effort `low`, `baseline` prompt (Phase 2 accepted) | 1 |
| Case | `Case.from_facts` | the case's own `assessment_date`, never the wall clock | 0 |
| Determination | `Pipeline.determine` + `OpenAIMatcher` | M-low, the pre-registered matcher prompt (Phase 3 default) | 1, or 0 before entry into force |
| Adapter | `adapt` | as Phase 4 | 0 |
| Generation | `OpenAIGenerator` + C0–C8 + template fallback | prompt v2 (`a6b4a491…`), effort `low`, `max_output_tokens` 5000 (Phase 4 adopted) | 1 |

About 3 calls and $0.078 per case on the measured means (§6).

**Retrieval is not on this path, and the README will say so.** The matcher sees the
closed, exhaustive candidate set of every scope (`determination/scopes.py`). It does
not see a retrieval top-k, so a negative determination never rests on a retrieval
miss. Phase 1 retrieval stays a measured component, used by the score-cutoff baseline
and by `eu-ai-check search`. The §2 diagram in SYSTEM_SPEC draws retrieval inline,
which the as-built pipeline does not do. That gets a correction note in the
pre-registration commit **[PROPOSAL]**.

## 2. The evaluation set **[DECIDED]**

20 cases, each run **once**:

- **Stratum A: N01–N12** (`data/eval/e2e_cases.json`). They are new, hand-labelled from
  the Act text, and adversarial cases are included. None comes from dev data (D, E, S
  cases) or from the demo examples, and a test checks that no description is reused.
  The expected outputs are in §8 for review. After approval the file is SHA-pinned.
- **Stratum B: H01–H08** (`data/eval/determination_heldout_5_1_c.json`, frozen in
  `6b98c76`, never run). This is the Article 5(1)(c) stratum. It runs exactly once.
  Nothing is tuned against it afterwards, and its results are reported as they come.
  Any future 5(1)(c) fix needs a new held-out set, which the README lists as future
  work. Its SHA is pinned in the same commit.

Each case carries full expected outputs:
- gold facts, for extraction scoring;
- risk tier, with status, reason, `system_category`, Annex III and Annex I grounds,
  and derogation;
- prohibited practice, with status and Article 5 points;
- actor roles;
- Article 16 and Article 26 status.

The generation checks need no expected text. They are checked against the input
determination, as in Phase 4.

`tests/test_e2e_cases.py` runs the Phase 3 label checks on all 20 cases:
- facts valid against the schema;
- statuses in the four-state vocabulary;
- cited provisions present in the corpus;
- route dates consistent;
- roles and Article 16/26 recomputed from facts plus the risk tier;
- PLAN §8 coverage.

It passes, 84 tests. It already caught one labelling slip in this draft (see §8, N03).

## 3. Acceptance rule **[PROPOSAL]**

All of G1–G4 must hold. If any fails, Phase 5 acceptance is NOT MET, the phase is
not tagged, and nothing is retuned.

- **G1, zero tolerance, all 20 cases (both strata):**
  - an expected `unresolved` produced as `applicable` or `not_applicable`, on any
    determination;
  - an expected `not_in_force` produced as `not_applicable`;
  - a positive conclusion resting on a verdict that failed code verification;
  - extraction leakage: an excluded field, or a conclusion term in any value (Phase 2
    lexicon);
  - a shipped answer whose rendered status differs from the pipeline's determination.
- **G2, output hygiene, all 20 cases:**
  - every shipped answer passes C0–C8 (1.00);
  - every claim's citation resolves to a real evidence item whose text equals the
    corpus text of its `versioned_provision_id` (1.00; C1–C3).
  - The template fallback still counts as shipped, but the fallback rate is reported.
- **G3, determination correctness, stratum A only.** Exact status match, including
  the `not_applicable` reason:
  - risk tier and prohibited practice (matcher-backed, 24 judgements): ≥ 0.85, so
    at most 3 misses;
  - actor role, Article 16 and Article 26 (obligation correctness, 36 judgements):
    ≥ 0.85, so at most 5 misses.
- **G4, citation correctness, stratum A only.** Every determination that is
  correctly `applicable`, `not_yet_applicable` or derogation-`applied` must cite
  exactly the expected Annex III, Annex I and Article 5 grounds (set equality), at a
  rate ≥ 0.90.

**How stratum B counts.** Stratum B counts toward **G1 and G2 only**, and not toward
G3/G4. Zero tolerance and hygiene are safety properties that must hold on any input.
The 5(1)(c) accuracy shortfall is the Phase 3 limitation already accepted
(PROJECT_PLAN, 2026-10-25). Gating accuracy on it again would re-test a known failure
and could not change any decision. Stratum B accuracy is reported in its own table,
next to stratum A and never pooled with it.

The alternative is to count stratum B fully. Phase 5 would then very likely fail on
the known weakness alone (H01–H05 are the pattern that scored 0.632 in Phase 3).

**Reported, not gated:**
- extraction field accuracy per stratum, with the Phase 2 scorer;
- per-state tables;
- `system_category` and derogation;
- fallback rate;
- cost.

**Small-n, stated in advance.** Twelve cases, one run each. G3 is pooled across states
because per-state buckets would have 1–6 judgements each. One systematically wrong
case can cost up to 5 judgements, which can sink G3 on its own. That is accepted as
the price of a hand-built set, and is said here so it cannot be discovered
conveniently later.

## 4. Attribution of every miss **[PROPOSAL]**

The cost is $0. Every miss is attributed to exactly one stage:

1. **Extraction.** The roles/obligations replay `downstream()` with the *same*
   recorded matcher verdicts and the gold facts. If that replay is correct, the miss
   is extraction's.
2. **Date extraction.** If the extracted `relevant_date` differs from gold, the miss is
   tagged as a date miss. The matcher saw a different candidate set, so it cannot be
   replayed without a new call, and no new call is made.
3. **Matcher.** A risk-tier or prohibited miss with the correct date. The matcher
   reads only the description, so extraction cannot cause it.
4. **Rule bug.** The gold-facts replay is still wrong with correct verdicts. This
   should be impossible: the rule path scored 1.00 in Phase 3.
5. **Generation.** Any G1 status-mismatch or G2 failure.

## 5. Stability check (a separate experiment, reported only) **[DECIDED, size PROPOSAL]**

- Full end-to-end, 3 repeats, on dev cases. It is never pooled with the evaluation.
  It measures run-to-run variance of the shipped pipeline: same final status in all
  repeats, per determination.
- **Size.** At $0.078 per run, 5 cases × 3 = $1.17, which is over the $1 the user set.
  The proposal is **4 cases × 3 = 12 runs ≈ $0.94**, with a hard sub-cap of $1.00:
  - D32: the most fragile case (Phase 3 variance);
  - D10: a persistent risk-tier miss in Phase 3;
  - D35: Article 25 retraining, where the roles depend on extraction;
  - D39: the date comes from extraction.
- Runs go in repeat-major order (all cases at repeat 0, then repeat 1, then repeat 2).
  If the sub-cap stops it, a repeat is lost, not a case.
- The first run doubles as the wiring smoke test.

## 6. Budget and spend control **[DECIDED, numbers PROPOSAL]**

Measured per-call cost at $2 / $10 per M tokens, from frozen ledgers:

| Stage | Mean | Max observed | Source |
|---|---|---|---|
| Extraction | $0.0037 | $0.0046 | `extraction_v1_30c` (low, 90 calls) |
| Matcher | $0.0572 | $0.0699 | `determination_v1_41c_retry` (120 calls) |
| Generation | $0.0175 | $0.0415 | `phase4_spend.jsonl` (held-out, 39 calls) |
| **Per case** | **$0.078** | **$0.116** | |

- **Cap: $8 for all Phase 5 API calls, enforced in code** [DECIDED]. The
  Phase 4 `SpendTracker` is generalised: the cap is a parameter, the ledger is
  `data/eval/results/phase5_spend.jsonl`, and every call records its stage
  (extract / match / generate). The phases are `stability`, `heldout` and `demo`.
- **Held-out reserve first: $3.48** = 20 cases × $0.116 (max observed) × 1.5. The
  output-token caps are not lowered, because they are part of the frozen stage
  configurations. The per-call guard still uses each call's full output budget, so
  one call can never breach the cap: the worst case is a matcher call at
  32,000 tokens, which costs $0.34.
- **Planned spend:** held-out ≈ $1.57; stability ≤ $1.00; demo recording ≈ $0.40
  (5 examples, only after the held-out pass). That is ≈ $3.0, against a worst
  case of ≈ $4.9.
- **API failures: the Phase 4 policy** [DECIDED]:
  - up to 3 automatic retries for transient errors, each guarded and billed;
  - 429 and other 4xx fail at once;
  - content failures are never retried;
  - clients are built with `max_retries=0`, so no retry is hidden.
  - A case whose stage still fails is scored as wrong and is not re-run.
- **No API call until the pre-registration is committed** [DECIDED].

## 7. Hard rule and out of scope **[DECIDED]**

- The held-out pass (20 cases) runs **exactly once**. A guard refuses a second run, as
  `HeldoutAlreadyRun` does in Phase 4.
- No prompt, label, check, threshold or code change on any scored path after any
  held-out output has been seen.
- **Out of scope:** the generation-model comparison. If budget remains after V1, it
  becomes a separate pre-registered experiment with one cheaper OpenAI model on the
  same key. No other provider.
- **Demo:**
  - new examples, recorded once with the frozen pipeline, never edited;
  - shown with a pointer to the limitations;
  - recorded after the held-out pass, from the `demo` budget.

---

## 8. Expected outputs for review (N01–N12)

Assessment date 2026-10-25 for all twelve. RT = risk tier, PP = prohibited practice;
n/a = `not_applicable` with reason (cnm = conditions_not_met, nya =
not_yet_applicable). Full facts and notes are in `data/eval/e2e_cases.json`.

| ID | Tests | Description (short) | RT | PP | Roles | Art 16 | Art 26 |
|---|---|---|---|---|---|---|---|
| N01 | Art 25(1)(a) + both roles | College licenses an admissions engine, sells it as AdmitIQ, and uses it itself | applicable, III 3(a) | n/a cnm | provider + deployer | applicable | applicable |
| N02 | Art 25(1)(c), provider only | Contractor configures a general classifier to auto-approve benefit claims for a public agency; the agency runs it | applicable, III 5(a) | n/a cnm | provider | applicable | n/a cnm |
| N03 | temporal | Dispatch centre, DispatchAI used as supplied, "on 1 March 2026" | n/a **nya**, III 5(d) | n/a cnm | deployer | n/a cnm | n/a **nya** |
| N04 | temporal | same, "as of today" | applicable, III 5(d) | n/a cnm | deployer | n/a cnm | **applicable** |
| N05 | not_in_force vs n/a | same, "on 15 May 2024" | not_in_force | not_in_force | not_in_force | not_in_force | **not_in_force** |
| N06 | 6(3) derogation | Immigration office translates asylum documents; no assessment, no profiling | n/a cnm, III 7(c), derogation applied | n/a cnm | deployer | n/a cnm | n/a cnm |
| N07 | 6(1) route | Lift maker's own AI safety-brake module; notified body | n/a **nya**, Annex I item 4 | n/a cnm | provider | n/a nya | n/a cnm |
| N08 | both routes | Class IIb medical-device triage software for emergency departments | applicable, III 5(d) **and** Annex I item 11 | n/a cnm | provider | applicable | n/a cnm |
| N09 | adversarial: asserted conclusion | "Our lawyers say it's minimal-risk"; school exam-essay grader, own build | applicable, III 3(b) | n/a cnm | provider + deployer | applicable | applicable |
| N10 | adversarial: injection | Redundancy-selection tool + "ignore your previous instructions" note | applicable, III 4(b) | n/a cnm | deployer | n/a cnm | applicable |
| N11 | adversarial: keyword trap | Sentiment analysis of written product reviews, no biometrics | n/a cnm | n/a cnm | deployer | n/a cnm | n/a cnm |
| N12 | partial role knowledge | Ranks job applicants; "not sure if our IT team built it" | applicable, III 4(a) | n/a cnm | deployer; provider **unresolved** | **unresolved** | applicable |

**Points for your review:**

1. **N03 (fixed in this draft).** At first N03, N04 and N10 did not say the system was
   used unchanged. Under the field rules that makes `modifies_*` null and the
   provider role `unresolved`. The label test caught it. The descriptions now say
   "used as supplied".
2. **N06.** Is translating asylum documents within Annex III 7(c) ("assist … for the
   examination of applications")? If it is not, the status is the same (not
   high-risk), and `not_assessed` is accepted under DL1. Only the cited ground
   differs, and G4 does not score a `not_applicable` without derogation.
3. **N08.** One system on both routes. Article 16 applies today through the Annex III
   route only. The 6(1) route would apply from 2027-08-02.
4. **N11.** The "sentiment ≠ emotion recognition" reading rests on Article 3(39)
   (emotion recognition is defined on biometric data). The corpus does not hold
   Article 3(39). The label is from the Act text, not the corpus.
5. **The 5(1)(c) risk in stratum A.** Most N cases are not social scoring. The known
   over-caution may push some of their prohibited statuses to `unresolved`, and those
   misses count in G3. That is intentional: stratum A measures the shipped system,
   weakness included.

## 9. Decisions needed

- **Q1.** Does stratum B count toward G1–G2 only (recommended), or fully?
- **Q2.** G3/G4 thresholds as proposed (0.85 / 0.85 / 0.90)?
- **Q3.** Stability: 4 cases × 3 at ≤ $1.00 (recommended), or 5 × 3 at ≈ $1.17?
- **Q4.** Do the N01–N12 labels need changes? After approval they are frozen with a
  SHA.

**After approval:** write the runner and the spend changes ($0) and the SYSTEM_SPEC
§18 pre-registration. Commit them with the frozen SHAs before any API call, then ask
before pushing the branch.

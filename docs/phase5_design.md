# Phase 5 — End-to-end evaluation and polish: design note

Status: **FROZEN** (2026-11-04, branch `phase-5/end-to-end`). The author approved the
§8 labels and the thresholds on 2026-11-04. The binding text is the Phase 5
pre-registration in SYSTEM_SPEC §18, which wins over this note wherever the two
differ. The case SHAs are pinned in `tests/test_e2e_cases.py`, and the runner is
`src/eu_ai_check/e2e/`. No Phase 5 API call has been made.

One change was made at freezing, before any API call: the stability cap went from
$1.10 to $1.50 (§5). The earlier [PROPOSAL] items (§4 attribution, §6 reserve) were
approved as written.

Grounded in:
- PROJECT_PLAN Phase 5 and §8 (including the Phase 3 deviation, `2a8f647`);
- SYSTEM_SPEC §18, "End-to-end" (obligation, citation and evidence correctness, with
  per-stage attribution), and the Phase 2–4 pre-registrations;
- the as-built code: `extraction/openai_extractor.py`, `determination/pipeline.py`,
  `generation/{adapter,generator,checks,spend}.py`;
- measured costs and results from the frozen ledgers and runs (§3.1, §3.2, §6).

---

## 1. What "end-to-end" runs

One case is one free-text description plus an assessment date. The runner chains the
shipped configuration of every stage, with nothing gold in between:

| Stage | Component | Frozen configuration | Calls |
|---|---|---|---|
| Extraction | `OpenAIExtractor` | `gpt-6.1-sol`, effort `low`, `baseline` prompt (Phase 2 accepted) | 1 |
| Case | `Case.from_facts` | the case's own `assessment_date`, never the wall clock | 0 |
| Determination | `Pipeline.determine` + `OpenAIMatcher` | M-low, the pre-registered matcher prompt (Phase 3 default) | 1, or 0 before entry into force |
| Adapter | `adapt` | as Phase 4 | 0 |
| Generation | `OpenAIGenerator` + C0–C8 + template fallback | prompt v2 (`a6b4a491…`), effort `low`, `max_output_tokens` 5000 (Phase 4 adopted) | 1 |

That is about 3 calls and $0.078 per case on the measured means (§6).

**Pinned clock [DECIDED].** No expected output may change as calendar time passes.
- The runner pins its evaluation clock to **2026-10-25**, a constant tested in code.
  It never reads the wall clock.
- Each case's own `assessment_date` is fixed in its file: 2026-10-25 for N01–N12 and
  2026-10-04 for H01–H08. When a description states no date, `Case` uses that
  assessment date.
- Descriptions use explicit dates, never "today". A test rejects relative time words.
- The date logic is unchanged: Annex III obligations apply from 2026-08-02, and the
  Annex I route from 2027-08-02.

**Corpus snapshot [DECIDED].** All expected outputs are relative to the frozen corpus
snapshot, `data/corpus/provisions.json`. Its 110 provisions are the Act's text as in
force from 2024-08-01, with no amendments; every provision has
`text_valid_from = 2024-08-01`. A later amendment, such as the pending Digital Omnibus
change to Article 5, would need a new corpus version. It would not silently change
these labels.

**Retrieval is not on this path [DECIDED: recorded].** The matcher sees the closed,
exhaustive candidate set of every scope (`determination/scopes.py`), never a retrieval
top-k. Phase 1 retrieval stays a measured component, used by the score-cutoff baseline
and by `eu-ai-check search`. This is recorded as a dated note in SYSTEM_SPEC §18
(2026-10-25); the §2 diagram is left as written. The README states it too.

## 2. The evaluation set **[DECIDED]**

20 cases, each run **once**.

**Stratum A: N01–N12** (`data/eval/e2e_cases.json`):
- new cases, hand-labelled from the Act text, adversarial cases included;
- none comes from dev data (D, E or S cases) or from the demo examples, and a test
  checks that no description is reused;
- the expected outputs are in §8 for review;
- after approval the file is SHA-pinned.

**Stratum B: H01–H08** (`data/eval/determination_heldout_5_1_c.json`, frozen in
`53eeeec`, never run):
- the Article 5(1)(c) stratum;
- it runs exactly once, nothing is tuned against it afterwards, and its results are
  reported as they come;
- any future 5(1)(c) fix needs a new held-out set, which the README lists as future
  work;
- its SHA is pinned in the same commit.

**Expected outputs per case:**
- gold facts, for extraction scoring;
- risk tier, with status, reason, `system_category`, Annex III and Annex I grounds,
  and derogation;
- prohibited practice, with status and Article 5 points;
- actor roles;
- Article 16 and Article 26 status.

The generation checks need no expected text. They are checked against the input
determination, as in Phase 4.

**Label tests.** `tests/test_e2e_cases.py` runs the Phase 3 label checks on all 20
cases:
- facts are valid against the schema;
- statuses use the four-state vocabulary;
- every cited provision is in the corpus;
- route dates are consistent;
- roles and Article 16/26 are recomputed from the facts plus the risk tier;
- the PLAN §8 coverage is present.

It passes (84 tests) and has already caught one labelling slip (§8, point 5).

## 3. Acceptance rule

All of G1–G4 must hold. If any fails, Phase 5 acceptance is NOT MET, the phase is
not tagged, and nothing is retuned.

- **G1, zero tolerance, all 20 cases (both strata) [DECIDED: option (b)].** It is
  strict and counted by root cause. One root event, such as one matcher verdict
  flip, counts as one violation, and its cascade is listed with it. **Any violation
  fails Phase 5.** The violations are:
  - an expected `unresolved` produced as `applicable` or `not_applicable`, on any
    determination;
  - an expected `not_in_force` produced as `not_applicable`;
  - a positive conclusion resting on a verdict that failed code verification;
  - extraction leakage: an excluded field, or a conclusion term in any value (the
    Phase 2 lexicon);
  - a shipped answer whose rendered status differs from the pipeline's determination.
- **G2, output hygiene, all 20 cases:**
  - every shipped answer passes C0–C8 (1.00);
  - every claim's citation resolves to a real evidence item whose text equals the
    corpus text of its `versioned_provision_id` (C1–C3, 1.00);
  - a template fallback counts as shipped; the fallback rate is reported.
- **G3, determination correctness, stratum A only.** Exact status match, including
  the `not_applicable` reason:
  - **G3a:** risk tier and prohibited practice (matcher-backed, n = 24);
  - **G3b:** actor role, Article 16 and Article 26 (obligation correctness, n = 36).
- **G4, citation correctness, stratum A only.** A determination that comes out
  correctly `applicable`, `not_yet_applicable` or derogation-`applied` must cite
  exactly the expected Annex III, Annex I and Article 5 grounds (set equality).
  Stratum A has 10 such expected risk-tier determinations and no prohibited one, so
  n ≤ 10.
- **Thresholds [DECIDED: one rule for all gates, dev performance minus 0.05]:**
  - G3a ≥ 19/24;
  - G3b ≥ 33/36;
  - G4: 0 mismatches.

  The derivation is in §3.2.

**How stratum B counts [DECIDED].** Stratum B counts toward G1 and G2 only, not
toward G3/G4.
- Zero tolerance and hygiene are safety properties and must hold on any input.
- The 5(1)(c) accuracy shortfall is the Phase 3 limitation already accepted
  (PROJECT_PLAN, 2026-10-25).
- Stratum B accuracy is reported in its own table, next to stratum A, and never
  pooled with it.

**Reported, not gated:**
- extraction field accuracy per stratum, with the Phase 2 scorer;
- per-state tables;
- `system_category` and derogation;
- the fallback rate;
- cost.

### 3.1 The Q1 pre-check: would the D32 failure class trip G1? **Yes.**

The author's condition was to check on dev data, before freezing, whether the known
failure class would trip G1. If it would, the work stops so that the treatment can be
decided in advance.

*Data:* the official Phase 3 run (`determination_v1_41c_retry`: M-low, the
pre-registered prompt, 41 dev cases × 3, gold facts). G1 is applied as defined above,
to every determination. The extraction row uses the Phase 2 runs.

| What | Dev result |
|---|---|
| Determinations expected `unresolved` | 75 judgements: risk tier 15, prohibited 12, roles 15, Art 16 15, Art 26 18 |
| G1 violations | **3, all from one run: D32#2** |
| Their cause | One matcher verdict. Annex III 1(c) came back `does_not_match`, where the other two repeats said `insufficient`. Risk tier went `unresolved` → `not_applicable`, and Article 16 and Article 26 followed it. |
| Article 5(1)(c) on the 12 gold-`unresolved` prohibited runs (D30, D31, D32, D41) | `insufficient` 12/12. Prohibited `unresolved` 12/12, **0 violations**. |
| `not_in_force` → `not_applicable` | 0 |
| Extraction (Phase 2, `low`, 90 runs) | Gold-null values kept null: `develops_system` 33/33, `uses_system` 12/12, both `modifies_*` 147/147, branding 59/60 |

What the data shows:

1. **The 5(1)(c) over-caution does not trip G1 on its own.** With the shipped prompt
   it errs toward `insufficient`, that is, toward `unresolved`. That costs accuracy
   (Phase 3 prohibited `not_applicable`: 0.632), not safety. On dev, no 5(1)(c)
   verdict turned an `unresolved` case into a definite answer.
2. **The D32 class does trip G1.** A borderline `insufficient` flips to
   `does_not_match` from one run to the next. In Phase 3 the flip was on Annex III
   1(c), not on 5(1)(c).
   - Rate: 1 run in 123 (0.8%), or 1 in 15 runs where the risk tier was expected
     `unresolved`.
   - One flip produces up to 3 violations, because the obligations inherit the risk
     tier.
3. **The held-out set is exposed to it.**
   - Stratum B has expected-`unresolved` determinations on H07 (prohibited) and H08
     (risk tier, prohibited, Art 16, Art 26). Both hinge on borderline verdicts.
   - Stratum A has N12 (roles, Art 16). It depends on extraction keeping
     `develops_system` null, which never failed on dev.
4. **The post-hoc variants confirm the direction.** They are not shipped and are
   cited only as evidence. Pushing 5(1)(c) toward `does_not_match` (v2) produced D32
   zero-tolerance violations in 2 of 3 runs. Any 5(1)(c) fix trades accuracy for G1
   risk.

**Decided: option (b).** It applies to both strata. Stated plainly, as the
pre-registration will state it:
- A D32-type event (1 in 123 dev runs) on H07 or H08 **would fail G1, and with it
  Phase 5.**
- The held-out pass runs once and **cannot be rerun**, so such an event would stand.

The options as presented to the author:

- **(a) Strict, as drafted.** Any G1 violation in either stratum fails Phase 5. A
  D32-type flip on H07 or H08 would fail the phase.
- **(b) Strict, counted by root cause.** As (a), but violations are counted and
  reported per root event. One matcher flip counts as one violation, with its cascade
  listed. Any violation still fails Phase 5. Only the reporting changes.
- **(c) Strict on stratum A only.** Stratum B's G1 violations are reported but not
  gated. Stratum B is then treated like the accepted Phase 3 limitation. This weakens
  the "safe on every input" reason for counting stratum B at all.

G1 is fixed before any held-out call and is not redefined afterwards.

### 3.2 G3 and G4 thresholds as counts (Q2: option 2 for every gate, DECIDED)

**Justification base:** the official Phase 3 dev run, on gold facts.

| Measure | Dev result |
|---|---|
| Matcher-backed risk tier + prohibited | 204/246 = **0.829** |
| Roles + Article 16/26 | 349/369 = **0.946** |
| Citation grounds on correctly-statused positives | 99/102 = **0.971** |

End-to-end adds extraction error. For G3a it enters only through `relevant_date`,
because the matcher reads the description, not the extracted facts. For G3b it enters
through every actor fact. The 95% Wilson intervals are for the threshold count itself.

| Gate | Option | Pass if | Rate | 95% Wilson | Justification (one line) |
|---|---|---|---|---|---|
| G3a (n = 24) | 1, as drafted | ≥ 21/24 (≤ 3 misses) | 0.875 | 0.690–0.957 | Phase 3's locked matcher target, 0.85. **Above dev performance (0.829).** |
| G3a | 2, dev − 0.05 | ≥ 19/24 (≤ 5 misses) | 0.792 | 0.595–0.908 | Dev 0.829 minus a 0.05 margin = 0.779, rounded up to a whole count |
| G3b (n = 36) | 1, as drafted | ≥ 31/36 (≤ 5 misses) | 0.861 | 0.713–0.939 | Phase 3's 0.85 |
| G3b | 2, dev − 0.05 | ≥ 33/36 (≤ 3 misses) | 0.917 | 0.782–0.971 | Dev 0.946 minus 0.05 = 0.896, rounded up to a whole count |
| G4 (n ≤ 10) | 1, as drafted | ≤ 1 mismatch (9/10 at n = 10) | 0.900 | 0.596–0.982 | 0.90 |
| G4 | 2, dev − 0.05 | 0 mismatches (10/10) | 1.000 | 0.722–1.000 | Dev 0.971 minus 0.05 = 0.921, which allows no miss at n ≤ 10 |

**Decided:** option 2, one rule for all gates (dev performance minus 0.05):
- G3a ≥ 19/24;
- G3b ≥ 33/36;
- G4: 0 mismatches.

Every result is reported with its 95% Wilson interval, whether it passes or fails.
**G4 is brittle:** with n ≤ 10 and no miss allowed, a single wrong citation fails
the gate. The pre-registration says so.

Notes written when the options were presented:
- **G3a option 1 sits above the dev rate.** The 5(1)(c) over-caution also falls on
  stratum A: 11 of its 12 prohibited labels are `not_applicable`, and dev scored that
  bucket at 0.632. About 4 prohibited misses are therefore expected, and option 1
  allows 3. Option 1 would very likely fail on the known limitation alone. Option 2
  would probably pass, but narrowly.
- **The intervals are wide.** At n = 24, 19/24 is compatible with a true rate
  anywhere in 0.60–0.91. These gates detect gross failure. They do not estimate
  accuracy precisely.
- **Under a "dev minus margin" rule, G3b and G4 come out stricter than the draft,**
  because on dev the rule path and the citations were better than the matcher.

**Small-n, stated in advance.** Twelve cases, one run each. G3 is pooled across
states, because per-state buckets would have 1–6 judgements each. One systematically
wrong case can cost up to 5 judgements, which can sink G3 on its own. That is
accepted as the price of a hand-built set, and is said here so it cannot be
discovered conveniently later.

## 4. Attribution of every miss **[APPROVED 2026-11-04]**

Attribution costs $0. Every miss is attributed to exactly one stage:

1. **Extraction.** The roles and obligations are replayed through `downstream()` with
   the *same* recorded matcher verdicts and the gold facts. If the replay is correct,
   the miss is extraction's.
2. **Date extraction.** If the extracted `relevant_date` differs from gold, the miss is
   tagged as a date miss. The matcher saw a different candidate set, so the case
   cannot be replayed without a new call. No new call is made.
3. **Matcher.** A risk-tier or prohibited miss with the correct date. The matcher
   reads only the description.
4. **Rule bug.** The gold-facts replay is still wrong even with correct verdicts. This
   should be impossible: the rule path scored 1.00 in Phase 3.
5. **Generation.** Any G1 status mismatch or G2 failure.

## 5. Stability check (a separate experiment, reported only) **[DECIDED]**

- **Design:**
  - full end-to-end, on dev cases only;
  - never pooled with the evaluation;
  - measures the run-to-run variance of the shipped pipeline: whether each
    determination's final status is the same in every repeat.
- **Cases:** 4 cases × 3 repeats = 12 runs, about $0.94.
  - D32: the most fragile case (the Phase 3 variance);
  - D10: a persistent risk-tier miss in Phase 3;
  - D35: Article 25 retraining, so the roles depend on extraction;
  - D39: its date comes from extraction.
- **Hard cap: $1.50 for this experiment, enforced in code.** It was raised from
  $1.10 on 2026-11-04, before freezing and before any API call. The guard checks each
  call's worst case, and a matcher call can cost up to about $0.34. At $1.10 that
  check would have cut round 3 short at the planned spend (about $0.94). At $1.50 the
  last matcher call fits at mean cost (about $0.86 + $0.34). At the maximum observed
  case cost it still would not fit.
- **Statistics use complete rounds only.** A partial round is flagged and listed, and
  no conclusion is drawn from it.
- **Runs go in rounds:** repeat 1 of all four cases, then repeat 2, then repeat 3. If
  the cap stops it, a round is lost, not a case.
- **The first run doubles as the wiring smoke test.**

## 6. Budget and spend control **[DECIDED; reserve APPROVED 2026-11-04]**

Measured per-call cost at $2 / $10 per M tokens, from the frozen ledgers:

| Stage | Mean | Max observed | Source |
|---|---|---|---|
| Extraction | $0.0037 | $0.0046 | `extraction_v1_30c` (low, 90 calls) |
| Matcher | $0.0572 | $0.0699 | `determination_v1_41c_retry` (120 calls) |
| Generation | $0.0175 | $0.0415 | `phase4_spend.jsonl` (held-out, 39 calls) |
| **Per case** | **$0.078** | **$0.116** | |

- **Cap: $8 for all Phase 5 API calls, enforced in code.** The Phase 4
  `SpendTracker` is generalised:
  - the cap is a parameter;
  - the ledger is `data/eval/results/phase5_spend.jsonl`;
  - every call records its stage (extract / match / generate);
  - the phases are `stability` (with its own $1.50 cap), `heldout` and `demo`.
- **Held-out reserve, taken first: $3.48.** That is 20 cases × $0.116 (the maximum
  observed) × 1.5.
  - The output-token caps are not lowered, because they are part of the frozen stage
    configurations.
  - The per-call guard still uses each call's full output budget, so no single call
    can breach the cap. The worst single call is a matcher call at 32,000 output
    tokens, which costs $0.34.
- **Planned spend:**
  - held-out: about $1.57;
  - stability: about $0.94, capped at $1.50;
  - demo recording: about $0.40, for 5 examples, only after the held-out pass.

  That totals about $2.9, against a worst case of about $5.4 (the held-out reserve
  of $3.48, plus the stability cap of $1.50, plus the demo).
- **API failures follow the Phase 4 policy:**
  - up to 3 automatic retries for transient errors, each one guarded and billed;
  - HTTP 429 and other 4xx errors fail at once;
  - content failures are never retried;
  - clients are built with `max_retries=0`, so no retry is hidden;
  - a case whose stage still fails is scored as wrong and is not re-run.
- **No API call until the pre-registration is committed.**

## 7. Hard rule and out of scope **[DECIDED]**

- **The held-out pass (20 cases) runs exactly once.** A guard refuses a second run, as
  `HeldoutAlreadyRun` does in Phase 4.
- **No changes after the held-out pass.** No prompt, label, check, threshold or code
  on any scored path changes once any held-out output has been seen.
- **Out of scope: the generation-model comparison.** If budget remains after V1, it
  becomes a separate pre-registered experiment with one cheaper OpenAI model on the
  same key. No other provider.
- **Demo:**
  - new examples, recorded once with the frozen pipeline and never edited;
  - each shown with a pointer to the limitations;
  - recorded after the held-out pass, from the `demo` budget.
- **Added 2026-11-04 (SYSTEM_SPEC §18):**
  - *wiring-fix carve-out:* after stability output is seen, the only fixes allowed are
    those that change no stage configuration, prompt, label, check, threshold or
    scoring logic. Each one is tested, recorded as a dated §18 deviation note and shown
    to the author before the held-out pass;
  - *resume rule:* a crashed held-out pass may continue only with the cases that have
    no recorded result, and only if the scored-path git tree hash equals the one in the
    marker.

### 7.1 Limitations of this evaluation (stated before any run)

- **The new cases are not independent of the system's builder.** N01–N12 were written
  by the project author, with AI assistance, and no independent annotator reviewed
  their labels. They test the system against its builders' reading of the Act, not
  against an independent annotator's. Systematic misreadings shared by the builders
  and the labels would not show up as misses.
- **The G3 thresholds come from a different setting.** They derive from dev
  performance with gold facts (Phase 3). The end-to-end run also includes extraction
  error, so the same pipeline would be expected to score lower here than on dev.
  The thresholds are not adjusted for this (design §3.2).

---

## 8. Expected outputs for review (N01–N12)

The assessment date is 2026-10-25 for all twelve, and the runner's clock is pinned
to it. Where a case states a date, that date is used. All outputs are relative to the
2024-08-01 corpus snapshot. Abbreviations:
- RT = risk tier, PP = prohibited practice;
- n/a = `not_applicable`, with its reason: cnm = `conditions_not_met`, nya =
  `not_yet_applicable`.

Full facts and labelling notes are in `data/eval/e2e_cases.json`.

| ID | Tests | Description (short) | RT | PP | Roles | Art 16 | Art 26 |
|---|---|---|---|---|---|---|---|
| N01 | Art 25(1)(a) + both roles | College licenses an admissions engine, sells it as AdmitIQ, and uses it itself | applicable, III 3(a) | n/a cnm | provider + deployer | applicable | applicable |
| N02 | Art 25(1)(c), provider only | Contractor configures a general classifier, model unchanged, to auto-approve benefit claims for a public agency; the agency runs it | applicable, III 5(a) | n/a cnm | provider | applicable | n/a cnm |
| N03 | temporal | Dispatch centre, DispatchAI used as supplied, "on 1 March 2026" | n/a **nya**, III 5(d) | n/a cnm | deployer | n/a cnm | n/a **nya** |
| N04 | temporal | same, "on 1 October 2026" | applicable, III 5(d) | n/a cnm | deployer | n/a cnm | **applicable** |
| N05 | not_in_force vs n/a | same, "on 15 May 2024" | not_in_force | not_in_force | not_in_force | not_in_force | **not_in_force** |
| N06 | 6(3)(a) derogation | Immigration office sorts asylum documents into fixed folders for the caseworker; no assessment, no profiling | n/a cnm, III 7(c), derogation applied | n/a cnm | deployer | n/a cnm | n/a cnm |
| N07 | 6(1) route | Lift maker's own AI safety-brake module; Directive 2014/33/EU, notified body; "on 1 October 2026" | n/a **nya**, Annex I item 4 | n/a cnm | provider | n/a nya | n/a cnm |
| N08 | both routes | Class IIb medical-device triage software; Regulation (EU) 2017/745, notified body; "on 1 October 2026" | applicable (per route below) | n/a cnm | provider | applicable | n/a cnm |
| N09 | adversarial: asserted conclusion | "Our lawyers say it's minimal-risk"; school exam-essay grader, built in-house | applicable, III 3(b) | n/a cnm | provider + deployer | applicable | applicable |
| N10 | adversarial: injection | Redundancy-selection tool used as supplied, plus an "ignore your previous instructions" note | applicable, III 4(b) | n/a cnm | deployer | n/a cnm | applicable |
| N11 | adversarial: keyword trap | Rates the creditworthiness of bidding *companies*; no individual person assessed | n/a cnm | n/a cnm | deployer | n/a cnm | n/a cnm |
| N12 | partial role knowledge | Ranks job applicants; "not sure if our IT team built it" | applicable, III 4(a) | n/a cnm | deployer; provider **unresolved** | **unresolved** | applicable |

**N08 per route, on 2026-10-01** (in the file as `route_status` and `combination`;
a test checks them against the rule dates):

| Route | Ground | Established | Applies from | Status on 2026-10-01 |
|---|---|---|---|---|
| Article 6(2), Annex III | 5(d) emergency healthcare patient triage | yes | 2026-08-02 | **applicable** |
| Article 6(1), Annex I | item 11, Regulation (EU) 2017/745 + 6(1)(b) notified body | yes | 2027-08-02 | **not_applicable / not_yet_applicable** |

*How the routes combine (§9.1):*
- If either route is high-risk and applicable on `relevant_date`, the risk tier is
  `applicable`. One applicable route is enough, so the overall risk tier is
  **applicable**.
- `system_category` lists both established routes: `annex_iii_high_risk` and
  `article_6_1_high_risk`.
- Article 16 applies through its Annex III rule. Its Article 6(1) rule would apply
  only from 2027-08-02.

**Decidability check (the author's Q4 rule).** Every expected output must be decidable
from the frozen corpus and the description alone.
- Each case's grounds are in the corpus: Annex III, Annex I, Articles 5, 6, 16, 25 and
  26, Article 3(3), (4) and (23), and the applicability rules.
- Article 3(39) is not in the corpus, and no case needs it any more.

Changes in round 2:

1. **N06, reworked.** The translation draft hinged on a judgement call: does
   translating documents "assist the examination"? The new case *sorts the
   application's documents into fixed folders for the caseworker who examines the
   application*. That gives:
   - Annex III 7(c) on its text ("to assist competent public authorities for the
     examination of applications for asylum");
   - Article 6(3)(a) on its text ("a narrow procedural task");
   - an override that does not match, on the description's own words ("does not
     profile anyone").

   Residual judgement: the corpus does not define "narrow procedural task". Sorting
   into fixed folders is the plainest instance available.
2. **N08, clarified.** The description now names Regulation (EU) 2017/745, the
   instrument Annex I item 11 lists, instead of only "the Medical Device Regulation".
   Each ground is matched on its own text:
   - 6(1)(a): "the AI system is itself a product";
   - 6(1)(b): the notified body is stated;
   - Annex III 5(d): "emergency healthcare patient triage systems".
3. **N07, clarified the same way.** The description names "Directive 2014/33/EU" for
   Annex I item 4.
4. **N11, replaced.** The sentiment-analysis draft needed Article 3(39), which is not
   in the corpus, to rule out emotion recognition. The new trap is creditworthiness
   scoring of *companies*. Both negatives are decidable from the corpus text:
   - Annex III 5(b) covers "the creditworthiness of natural persons";
   - Article 5(1)(c) covers "natural persons or groups of persons".
5. **N03, N04 and N10, fixed in round 1.** The descriptions now say "used as
   supplied". Without that, the field rules make `modifies_*` null and the provider
   role `unresolved`. N02, N06 and N11 say it too.
6. **The 5(1)(c) risk in stratum A is unchanged, and intentional (§3.2).** Stratum A
   measures the shipped system, weakness included.
7. **Round 3: time stability.** N04 ("as of today") and N07 and N08 (no date) now ask
   about "1 October 2026". That date is after the Annex III date and before the
   Annex I date, so every status is unchanged.

## 9. Decisions

- **Q1: DECIDED, option (b).**
  - G1 is strict and counted by root cause; any violation fails Phase 5.
  - Stratum B counts toward G1–G2 only.
  - A D32-type event on H07 or H08 would fail G1, and the held-out pass cannot be
    rerun.
- **Q2: DECIDED, option 2 for every gate** (dev performance minus 0.05):
  - G3a ≥ 19/24, G3b ≥ 33/36, G4 0 mismatches;
  - results are always reported with Wilson intervals;
  - G4 is brittle at n ≤ 10.
- **Q3: DECIDED.** D32, D10, D35 and D39 × 3, in rounds. The hard cap was $1.10,
  raised to $1.50 on 2026-11-04 before freezing (§5).
- **Q4: DECIDED, labels approved against the Act text, with two round-3 changes.**
  - Time stability: a pinned clock, and explicit dates in N04, N07 and N08.
  - N08's expected output is given per route.

**Frozen 2026-11-04.** The author approved the labels and thresholds. The
pre-registration is in SYSTEM_SPEC §18. Next, each step only with the author's
go-ahead: the stability check, then the held-out pass.

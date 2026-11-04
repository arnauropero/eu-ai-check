# EU AI Check

A grounded compliance checker for the EU AI Act (Regulation (EU) 2024/1689). It takes
a free-text description of an AI system and determines four things:
- the system's risk tier;
- whether it is a prohibited practice;
- the organisation's role (provider or deployer);
- which Article 16 and Article 26 obligations apply.

Every determination cites the provisions it rests on. Each one comes out as
`applicable`, `not_applicable`, `unresolved` or `not_in_force`.

## In plain words

**The question it answers.** "Which EU AI Act rules apply to my AI system, and to
my organisation, on a given date?" You describe the system in your own words,
including what it does, who built it, who uses it and the date you are asking
about.

**What you get back.**
- **Four determinations:**
  - the risk tier;
  - whether the use is a prohibited practice;
  - your role (provider and/or deployer);
  - whether the Article 16 (provider) and Article 26 (deployer) obligations apply.
- **Each determination comes with:**
  - one of four statuses: applies, does not apply, could not be determined, or not
    yet in force on that date;
  - the exact provisions it rests on, quoted from the Act;
  - when it could not be determined, a list of what you would need to tell it.

**What it is not.**
- It is not legal advice.
- It covers only the provisions listed above.
- It reads the Act as published on 2024-08-01, with no later amendments.
- When the description leaves a point open, it says so rather than guess, so expect
  `unresolved` answers.

**How it works, in four steps:**
1. **Fact extraction.** A language model turns your description into a fixed set of
   plain facts (who built it, who uses it, what it does, which date). It is not
   allowed to draw legal conclusions.
2. **Provision matching.** A second model call checks your description against every
   candidate provision in the scope: Annex III, Article 5, Article 6(3), and Annex I
   with Article 6(1). It must quote both your text and the Act, and code checks the
   quotes. A verdict that fails the check is downgraded to "insufficient".
3. **Rules.** Fixed code turns the facts and verdicts into the four determinations.
   This step applies the Act's dates and its rules on roles and obligations, and has
   no model in it.
4. **Answer.** A third model call writes short explanations. Code sets every status,
   citation and "what we would need to know" list, and checks the text. If the text
   fails a check, a plain template answer is shown instead.

**Trying it with your own key.**
- Setup:
  1. Install with `uv sync`.
  2. Set `OPENAI_API_KEY` to your own key. The project runs no hosted service and
     ships no key.
- Run:

  ```
  eu-ai-check assess "We run a chain of language schools. We built a tool that places each new student in a course level from an online test. Which AI Act rules apply to us on 1 November 2026?"
  ```

  This runs the full pipeline (extraction, matching, rules, answer with checks and
  template fallback) and prints the answer. At the end it prints the cost of the
  run and a pointer to the limitations.
- **Dates.** `--date YYYY-MM-DD` sets the assessment date; the default is today. A
  date stated in the description takes precedence.
- **Cost.**
  - An assessment costs about **$0.08** on average (held-out pass, `gpt-6.1-sol` at
    $2 / $10 per million input / output tokens). The dearest held-out case cost
    $0.101.
  - `--max-cost` (default $0.75) caps the run. Before any call, the command checks
    the worst case of all three calls at their full output budgets, about $0.60,
    and refuses to start if that is over the cap. Every call is then guarded again,
    so a run cannot spend more than the cap.
- `assess` writes no files.
- To see full answers without a key, read the recorded demo in
  `data/demo/recording.md`.

**Results at a glance.**
- The pre-registered end-to-end evaluation passed all four of its gates, two of them
  by a single event.
- Every miss was cautious: "could not be determined" where a definite answer was
  expected.
- See [End-to-end results (Phase 5)](#end-to-end-results-phase-5) and
  [Limitations](#limitations).

## Legal text version

All results, and all expected outputs in the evaluation sets, are relative to one
frozen corpus snapshot: the Act as in force from 2024-08-01, with no amendments
(`data/corpus/provisions.json`, 110 provisions). An amendment, such as the pending
Digital Omnibus change to Article 5, would need a new corpus version. The answers do
not track legislative changes automatically. Evaluation dates are fixed in the eval
files, so no expected output changes as calendar time passes.

## Pipeline as built

Fact extraction (LLM, schema-constrained) → grounded determination (rules plus one
constrained LLM provision matcher) → generation (claims-first, citation-checked, with
a template fallback).

**Retrieval is not on the determination path.** SYSTEM_SPEC.md §2 draws hybrid
retrieval between extraction and determination, but the shipped pipeline does not
work that way. The provision matcher is given the complete set of candidate
provisions in every scope, not a retrieved top-k, so a "does not apply" answer never
rests on a retrieval miss. Evidence text comes from the corpus by provision id.
Retrieval was built and measured in Phase 1 and is still used by the score-cutoff
baseline and by `eu-ai-check search`, but the end-to-end evaluation does not exercise
it. See the dated note in SYSTEM_SPEC.md §18 (2026-10-25).

## End-to-end results (Phase 5)

The end-to-end evaluation was pre-registered in SYSTEM_SPEC.md §18 before any API
call. It ran once, on 2026-11-04, over 20 cases:
- N01–N12: new, hand-labelled cases;
- H01–H08: the Article 5(1)(c) held-out set.

Every stage used its frozen configuration: extraction, the provision matcher, the
adapter and the generator, all on `gpt-6.1-sol`. The pass was not interrupted or
resumed. Results are in `data/eval/results/e2e_heldout.json` and the per-case records
are in `e2e_heldout.runs.jsonl`.

**Reproducing the run.**
- The pass ran at commit `405cd0c`. Its scored-path tree hash, recorded in
  `data/eval/results/e2e_heldout.started` before the first call, is
  `9e200aa486dbaf050dff4cc5d2440aa8283fbc059a76eaefa8a5058d3879c67c`.
- The scored paths are `src/eu_ai_check`, `data/corpus`, the two case files,
  `pyproject.toml` and `uv.lock` (SYSTEM_SPEC.md §18).
- To check it: `git checkout 405cd0c`, then `eu_ai_check.e2e.runner.scored_tree()`
  returns the same hash.

**All four acceptance gates passed:**

| Gate | Result | Threshold | 95% Wilson interval |
|---|---|---|---|
| G1, zero tolerance (all 20 cases) | 0 violations | 0 | — |
| G2, output hygiene (all 20 cases) | 20/20 | 20/20 | 0.839–1.000 |
| G3a, risk tier + prohibited practice (N01–N12) | 20/24 = 0.833 | ≥ 19/24 | 0.641–0.933 |
| G3b, actor roles + Article 16/26 (N01–N12) | 34/36 = 0.944 | ≥ 33/36 | 0.819–0.985 |
| G4, citation grounds (N01–N12) | 10/10, 0 mismatches | 0 mismatches | 0.722–1.000 |

**Two gates passed by a single event:**
- **G3a** passed at 20/24 against a threshold of 19. One more miss would have
  failed it.
- **G2** needed the last allowed retry. N06's extraction succeeded on its fourth
  and final attempt, after three connection errors. One more failure would have
  left N06 with no answer, and G2 would have failed.

**G1 allows no violation at all.**

**All 13 missed judgements were on the cautious side.** They fall in 9 of the 20
cases: N01, N02, N07, N10, N12, H02, H04, H06 and H08. Each produced `unresolved`
where the label had a definite answer. None produced a definite answer where the
label was `unresolved`. By stage:
- **Matcher (6):** the prohibited-practice determination of N01, N02, N10, N12, H02
  and H04. In each, the Article 5(1)(c) verdict was `insufficient`, which is the
  Phase 3 limitation.
- **Extraction (7):** N07 (role and Article 26), H02 and H06 (role and Article 16
  each), and H08 (role). In each, an actor fact came back null where the reference
  had a value.

**Other figures:**
- template fallback: 0/20;
- `system_category`: 11/11;
- spend: $1.61 for the held-out pass and $2.57 for all of Phase 5, against an $8
  cap. The spend ledger (`phase5_spend.jsonl`) sums to the same total.
- **typical cost per assessment:** about **$0.08** on average over the held-out
  pass (20 cases, three model calls each, at `gpt-6.1-sol` prices of $2 / $10 per
  million input / output tokens).
  - The dearest held-out case cost $0.101.
  - The dearest combination observed across the earlier phases' ledgers is $0.116
    per case, and the budget used it as the planning maximum.
  - A case dated before the Act entered into force needs no matcher call and costs
    about $0.007.

**Stratum B (H01–H08), reported but not gated:**

| Measure | Result | 95% Wilson interval |
|---|---|---|
| Risk tier + prohibited practice | 14/16 = 0.875 | 0.640–0.965 |
| Actor roles + Article 16/26 | 19/24 = 0.792 | 0.595–0.908 |

**Stability check** (dev cases D32, D10, D35 and D39, three repeats each, reported
only): 19 of 20 determinations gave the same final status in all three repeats. The
one that changed was D39's prohibited-practice status, on its Article 5(1)(c)
verdict. Results are in `data/eval/results/e2e_stability_v2.json`.

## Limitations

- **Passing G1 does not show that the D32 failure class is solved.**
  - In the stability check, D32 failed in all 3 repeats: Annex III 1(c) came back
    `does_not_match` ("attention, not emotion"). Its risk tier went from the expected
    `unresolved` to `not_applicable`, and Articles 16 and 26 followed it. In Phase 3
    the same case failed this way in 1 of 3 runs.
  - In the evaluation set, three cases have any expected-`unresolved`
    determination: N12, H07 and H08 (checked against the case files). N12's come
    from an actor fact the description leaves open, not from a matcher verdict. So
    the cases exposed to this failure class are H07 and H08. Each ran once. No case
    reproduces D32's own borderline (Annex III 1(c)).
  - G1 shows that this failure did not happen in those runs, not that it cannot
    happen.
- **The stratum B extraction scores reflect a mismatch between the reference labels
  and the extraction rules.**
  - The stratum B reference facts set `modifies_intended_purpose` and
    `modifies_safety_relevant_components` to `false` in all eight cases. The
    extraction rules return null when a description does not say the system is
    used as supplied, and for systems the organisation built itself.
  - Extraction followed the rules, so it scores 0/8 on both fields. The same
    mismatch is why the role misses of H02 and H06 are attributed to extraction.
  - Stratum B accuracy is reported, not gated.
- **A prohibited practice does not suppress the high-risk obligations.**
  - When a prohibited practice applies, the answer still lists the high-risk
    obligations as applying, without flagging the inconsistency.
  - In the held-out pass, H06 came out prohibited under Article 5(1)(c). Its answer
    still says "Article 26: applies to this case" and lists the Article 26
    obligations.
  - The Phase 4 raters reported the same thing (SYSTEM_SPEC.md §18). It comes from
    the shared determinations and renderer, not from the generator.
- **The checks verify citations and quotes, not the legal reasoning.**
  - The matcher must quote both the description and the provision, and the answer's
    claims must cite real evidence items. Code checks all of that.
  - No check judges whether applying a provision to the description is correct.
  - Demo example X05 shows the gap. The matcher marked Article 6(3)(b) (improving
    the result of a previously completed human activity), although the description
    mentions no previous human activity. The quotes were verbatim, so every check
    passed.
  - The matcher can also add grounds on its own: in demo example X04 it added
    Annex III 8(b) (influencing elections or voting behaviour), which nobody had
    anticipated for that example.
- **Behaviour on the Article 6(3) profiling question varies with the description.**
  - Suppose a system listed in Annex III meets an Article 6(3) condition (for
    example, a narrow procedural task). The derogation then hinges on whether the
    system profiles people. If the matcher judges that the description does not
    answer that, the risk tier stays `unresolved`, where a lawyer might apply the
    derogation.
  - Dev case D10 went that way. Article 6(3)(a) and (c) matched, the profiling
    question stayed undecided, and the risk tier was `unresolved` in all three
    stability repeats. Its label is `not_applicable`.
  - Demo example X05 went the other way. The matcher judged that "checks each job
    application for missing documents" answers the profiling question (no
    profiling), and applied the derogation.
  - A definite answer can therefore rest on an inference from a sparse
    description.
- **G2 mixes output quality with infrastructure noise.** The held-out pass made 70
  recorded call attempts: 59 completed and 11 transient connection errors, all
  recovered by the pre-registered retries. G2 passing depended on that recovery as
  well as on answer quality. The errors may be specific to the machine and network
  used for the run.
- **The new cases are not independent of the system's builder.** N01–N12 were
  written by the project author and Claude Code, and a second Claude session reviewed
  their labels. A misreading of the Act shared by the builders and the labels would
  not show up as a miss.
- **The G3 thresholds come from a different setting.** They are development
  performance minus 0.05, measured with gold facts in Phase 3. The end-to-end run
  also includes extraction error.
- **The intervals are wide.** With 24, 36 and 10 judgements, the gates detect gross
  failure. They do not estimate accuracy precisely. For example, 20/24 is
  compatible with a true rate anywhere from 0.64 to 0.93.
- **Retrieval is not on the product path.** See "Pipeline as built" above.
- **The legal text is frozen.** The corpus is the 2024-08-01 text, with no
  amendments (see "Legal text version" above).
- **Phase 3 acceptance was NOT MET.** The Article 5(1)(c) shortfall was accepted as
  a documented limitation. V1 will never claim that all five phases met their
  acceptance criteria.
- **Only `gpt-6.1-sol` was evaluated** as the model for extraction, matching and
  generation. The generation-model comparison was out of scope.

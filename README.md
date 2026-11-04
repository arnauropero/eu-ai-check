# EU AI Check

A grounded compliance checker for the EU AI Act (Regulation (EU) 2024/1689). It takes
a free-text description of an AI system and determines four things:
- the system's risk tier;
- whether it is a prohibited practice;
- the organisation's role (provider or deployer);
- which Article 16 and Article 26 obligations apply.

Every determination cites the provisions it rests on. Each one comes out as
`applicable`, `not_applicable`, `unresolved` or `not_in_force`.

> This README is being written as part of Phase 5. Architecture, rejected
> alternatives, known limitations and setup instructions will be added here.

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

**Three of the gates were each one event away from failing:**
- **G3a** passed at 20/24 against a threshold of 19, so one more miss would have
  failed it.
- **G2** needed the last allowed retry. N06's extraction succeeded on its fourth
  and final attempt, after three connection errors. One more failure would have
  left N06 with no answer, and G2 would have failed.
- **G1** allows no violation at all, so any single event would have failed it.

**All 13 misses were on the cautious side.** Each produced `unresolved` where the
label had a definite answer. None produced a definite answer where the label was
`unresolved`. By stage:
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
  - In the evaluation set, only H07 and H08 have expected-`unresolved`
    determinations that rest on borderline matcher verdicts. Each ran once. No case
    reproduces D32's own borderline (Annex III 1(c)).
  - G1 shows that this failure did not happen in those runs, not that it cannot
    happen.
- **The stratum B extraction scores reflect a labelling convention, not a system
  fault.**
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
- **The Article 6(3) profiling exception makes answers cautious.**
  - Suppose a system listed in Annex III meets an Article 6(3) condition (for
    example, a narrow procedural task), but its description does not say whether it
    profiles people. The profiling exception then stays undecided, and so does the
    risk tier.
  - A lawyer might apply the derogation in such a case, where the system says
    `unresolved`.
  - Dev case D10 shows this. Article 6(3)(a) and (c) matched, the profiling
    exception came back undecided, and the risk tier was `unresolved` in all three
    stability repeats. Its label is `not_applicable`.
- **G2 mixes output quality with infrastructure noise.** The held-out pass made 70
  recorded call attempts: 59 completed and 11 transient connection errors, all
  recovered by the pre-registered retries. G2 passing depended on that recovery as
  well as on answer quality.
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

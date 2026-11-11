# Evaluation details

The full record behind the summary in the [README](../README.md). Limitations are listed in [limitations.md](limitations.md).

## Terms used below

- **Pre-registered:** the cases, gates, thresholds and budget were written down and
  committed before the evaluation's first model call, so they could not be fitted to
  the results.
- **Held-out:** cases kept aside and never used while building or tuning the system.
  They are run once, at the end.
- **Stratum A / stratum B:** the two parts of the 20-case evaluation set.
  - Stratum A is N01–N12, new cases scored for accuracy.
  - Stratum B is H01–H08, an earlier held-out set on Article 5(1)(c), checked only
    for safety and output hygiene (G1 and G2).
- **Phases:** the stages the project was built in, from Phase 0 (the legal text) to
  Phase 5 (the end-to-end evaluation). Each had acceptance criteria fixed before its
  run.
- **Wilson interval:** the range of true rates that are plausible, with 95%
  confidence, given the result on a small number of cases. The fewer the cases, the
  wider the range.

## Legal text version

All results, and all expected outputs in the evaluation sets, are relative to one
frozen corpus snapshot: the Act as in force from 2024-08-01, with no amendments
(`data/corpus/provisions.json`, 110 provisions). Regulation (EU) 2026/1744 (Digital
Omnibus on AI), in force since 27 July 2026, has since amended the Act. It is not in
the corpus; including it would need a new corpus version. The results here are
unaffected: they measure the system against the 2024 text, on which every expected
output is based. The answers do not track legislative changes automatically. Evaluation dates are fixed in the eval
files, so no expected output changes as calendar time passes.

## Pipeline as built

Fact extraction (LLM, schema-constrained) → grounded determination (rules plus one
constrained LLM provision matcher) → generation (claims-first, citation-checked, with
a template fallback).

**Retrieval is not on the determination path.** SYSTEM_SPEC.md §2 draws hybrid
retrieval between extraction and determination, but the shipped pipeline does not
work that way. The provision matcher is given the complete set of candidate
provisions in every scope, not a retrieved top-k, so a `not_applicable` answer never
rests on a retrieval miss. Evidence text comes from the corpus by provision id.
Retrieval was built and measured in Phase 1 and is still used by the score-cutoff
baseline and by `eu-ai-check search`, but the end-to-end evaluation does not exercise
it. See the dated note in SYSTEM_SPEC.md §18 (2026-10-25).

## End-to-end results (Phase 5)

The end-to-end evaluation was pre-registered in SYSTEM_SPEC.md §18 before any API
call. It ran once, on 2026-11-04, over 20 cases:
- N01–N12: new, hand-labelled cases;
- H01–H08: the Article 5(1)(c) held-out set.

One presentation change was made after this pass: the prohibited-practice notice
(see [limitations.md](limitations.md) and SYSTEM_SPEC.md §18, 2026-11-11). It
changes no status, citation or check, and the results below were not re-scored.

Every stage used its frozen configuration: extraction, the provision matcher, the
adapter and the generator, all on `gpt-6.1-sol`. The pass was not interrupted or
resumed. Results are in `data/eval/results/e2e_heldout.json` and the per-case records
are in `e2e_heldout.runs.jsonl`.

**Reproducing the run.**
- The pass ran at commit `3a3aad8`. Its scored-path tree hash, recorded in
  `data/eval/results/e2e_heldout.started` before the first call, is
  `9e200aa486dbaf050dff4cc5d2440aa8283fbc059a76eaefa8a5058d3879c67c`.
- The scored paths are `src/eu_ai_check`, `data/corpus`, the two case files,
  `pyproject.toml` and `uv.lock` (SYSTEM_SPEC.md §18).
- To check it: `git checkout 3a3aad8`, then `eu_ai_check.e2e.runner.scored_tree()`
  returns the same hash.

**All four acceptance gates passed:**

| Gate | Result | Threshold | 95% Wilson interval |
|---|---|---|---|
| G1, zero tolerance (all 20 cases) | 0 violations | 0 | — |
| G2, output hygiene (all 20 cases) | 20/20 | 20/20 | 0.839–1.000 |
| G3a, risk tier + prohibited practice (N01–N12) | 20/24 = 0.833 | ≥ 19/24 | 0.641–0.933 |
| G3b, actor roles + Article 16/26 (N01–N12) | 34/36 = 0.944 | ≥ 33/36 | 0.819–0.985 |
| G4, citation grounds (N01–N12) | 10/10, 0 mismatches | 0 mismatches | 0.722–1.000 |

**Margins:**
- **G3a** (20/24 against ≥ 19) and **G3b** (34/36 against ≥ 33) each passed with
  one miss to spare.
- **G2 passed with no margin.** N06's extraction succeeded on its fourth and last
  allowed attempt, after three connection errors. One more failure would have left
  N06 with no answer, and G2 would have failed.
- **G1 and G4 allow no miss at all.** G1 had no violation, and G4 was 10/10.

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
- **spend**, against an $8 cap for all of Phase 5:
  - evaluation: $2.57 (the held-out pass, $1.61, plus the stability check);
  - demo recording: $0.35;
  - `assess` smoke test: $0.09;
  - total: about $3.01.

  The spend ledger (`phase5_spend.jsonl`) holds the evaluation and demo calls.
  `assess` runs are not written to it.
- **typical cost per assessment:** about **$0.08** on average over the held-out
  pass (20 cases, three model calls each, at `gpt-6.1-sol` prices of $2 / $10 per
  million input / output tokens).
  - The most expensive held-out case cost $0.101.
  - The most expensive combination observed across the earlier phases' ledgers is
    $0.116 per case, and the budget used it as the planning maximum.
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


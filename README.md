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

## Known limitations of the evaluation

These are stated before any Phase 5 run (SYSTEM_SPEC.md §18, Phase 5
pre-registration). The full list of limitations will be added here.

- **The end-to-end cases are not independent of the system's builder.** The 12 new
  cases (N01–N12) were written by the project author and Claude Code. A second
  Claude session reviewed their labels. A misreading of the Act shared by the
  builders and the labels would not show up as a miss.
- **The G3 thresholds come from a different setting.** They come from development
  performance measured with gold (hand-labelled) facts. The end-to-end run also
  includes fact-extraction error, so it is a harder setting than the one the
  thresholds came from.

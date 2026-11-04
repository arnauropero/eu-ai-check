# Phase 5 — status and handoff (2026-11-04)

Branch `phase-5/end-to-end`. Everything is committed locally. **Nothing has been
pushed and no Phase 5 API call has been made ($0 spent).** The eval set and the
pre-registration are **frozen**.

## Frozen on 2026-11-04

- **Pre-registration:** SYSTEM_SPEC §18, "End-to-end — Phase 5 pre-registration".
  It is binding and wins over `docs/phase5_design.md` wherever they differ.
- **Eval set:** N01–N12 (`data/eval/e2e_cases.json`) and H01–H08
  (`data/eval/determination_heldout_5_1_c.json`, unchanged since `6b98c76`). The
  SHAs of both are pinned in `tests/test_e2e_cases.py`.
- **Gates:**
  - G1: strict, counted by root cause, both strata;
  - G2: output hygiene, both strata;
  - G3a ≥ 19/24 and G3b ≥ 33/36, stratum A;
  - G4: 0 citation mismatches (n ≤ 10, brittle), stratum A.

  Every result is reported with its Wilson interval.
- **Budget:**
  - an $8 cap on all Phase 5 calls;
  - a $3.48 held-out reserve, set aside first;
  - a stability cap of **$1.50**, raised from $1.10 before freezing and before any
    API call, because the worst-case-per-call check would have cut round 3 short;
  - stability statistics use complete rounds only.
- **Additions (2026-11-04, before any push or API call):** the wiring-fix carve-out
  and the held-out resume rule (scored-path git tree hash in the marker), in §18.
  The limitations of the eval set (builder-written cases, G3 thresholds from gold-fact
  dev runs) are in design §7.1 and the README.
- **Clock:** pinned to 2026-10-25. No system-clock call on the evaluation path
  (tested). Expected outputs are relative to the 2024-08-01 corpus snapshot.

## Pre-freeze checks (2026-11-04, all passed)

- **Dates:**
  - N04, N07 and N08 say "1 October 2026";
  - no relative time word appears in any of the 20 descriptions. The only regex hit
    is H05's "buy-now-pay-later", a product name.
- **Clock:**
  - the runner's clock is the constant 2026-10-25;
  - a static scan of its import closure finds one clock call, the Phase 4 ledger's
    `datetime.now`;
  - a runtime test shows that call is never reached on the Phase 5 path.
- **N08:** expected per route, as decided.
- **Thresholds and hard rules:** in the pre-registration as decided.
- **Corpus:** all 110 provisions have `text_valid_from = 2024-08-01`.

## Built (no API calls)

- **`src/eu_ai_check/e2e/runner.py`:**
  - the stage chain, with the frozen configuration enforced;
  - the API-failure policy;
  - stability in rounds, with the first run as the smoke test;
  - the single held-out pass, with a marker and `HeldoutAlreadyRun`.
- **`src/eu_ai_check/e2e/scoring.py`:** the gates, Wilson intervals, attribution
  replay, root-cause G1 and stability over complete rounds.
- **`src/eu_ai_check/e2e/spend.py`:** the Phase 5 tracker and ledger
  `data/eval/results/phase5_spend.jsonl`. The Phase 4 tracker is not touched.
- **CLI:** `eu-ai-check eval-e2e --mode status|stability|heldout`.
- **Tests:** `tests/test_e2e_runner.py` and `tests/test_e2e_spend.py`, with the fake
  client in `tests/e2e_fakes.py`. A fake run is a **plumbing check only**, never a
  result.

## Next steps, each only with the user's go-ahead

**Status (2026-11-04, end of day).**
- Done: the stability check (v2), the held-out pass (acceptance MET, G1–G4 pass),
  the demo recording, `eu-ai-check assess` and the README.
- The README commit `3dd99f5` is local only. The branch on origin is at `2acb180`.
- No PR, merge or tag yet.

Remaining work:
1. The user reads the README as a first-time visitor.
2. If it fails the 60-second test, trim the README further.
3. Open the PR `phase-5/end-to-end` → `master`, merge it, and tag `phase-5`. Each
   step only with the user's explicit approval.
4. Update PROJECT_PLAN.
5. Build the visual demo page.

Open point: the README limitation "V1 will never claim that all five phases met
their acceptance criteria" uses "five". PROJECT_PLAN numbers the phases 0–5, so there
are six. Left unchanged pending the user's decision.

# Phase 5 — status and handoff (2026-10-25, end of day)

Branch `phase-5/end-to-end`. Everything is committed locally. **Nothing has been
pushed, nothing is frozen, and no Phase 5 API call has been made ($0 spent).** The
design is in `docs/phase5_design.md`.

## Decisions taken

- **Phase 3:** closed as a documented limitation (no further attempt in V1). V1's
  acceptance carries a stated deviation (PROJECT_PLAN, `ce8e60d`).
- **Q1, G1 treatment: option (b).**
  - G1 is strict and counted by root cause. Any violation fails Phase 5.
  - Stratum B (H01–H08, Article 5(1)(c)) counts toward G1–G2 only, not toward
    accuracy.
  - The pre-registration will state plainly that a D32-type event (1 in 123 dev runs)
    on H07 or H08 would fail G1, and that the held-out pass cannot be rerun.
- **Q2, thresholds: option 2 for every gate**, one rule (dev performance minus 0.05):

  | Gate | Pass if | 95% Wilson | Dev base (Phase 3, gold facts) |
  |---|---|---|---|
  | G3a, risk tier + prohibited (n = 24) | ≥ 19/24 | 0.595–0.908 | 204/246 = 0.829 |
  | G3b, roles + Art 16/26 (n = 36) | ≥ 33/36 | 0.782–0.971 | 349/369 = 0.946 |
  | G4, citation grounds (n ≤ 10) | 0 mismatches | 0.722–1.000 (10/10) | 99/102 = 0.971 |

  Results are always reported with Wilson intervals. G4 is brittle: one wrong
  citation fails it.
- **Q3, stability check:** D32, D10, D35 and D39 × 3, run in rounds. Hard cap $1.10.
  Reported only.
- **Q4, labels:** N01–N12 approved against the Act text, with two changes, made
  tonight:
  - time stability;
  - N08 per route.
- **Other decisions already in force:**
  - $8 cap for all Phase 5 API calls, enforced in code, with the held-out reserve
    taken first;
  - the Phase 4 spend tracker and API-failure policy;
  - the model comparison is out of scope;
  - no API call until the pre-registration is committed.

## What changed tonight

- `data/eval/e2e_cases.json`:
  - N04, N07 and N08 now ask about "1 October 2026" instead of "today" or no date.
    No status changed.
  - N08 carries `route_status` (Annex III 5(d): applicable; Annex I item 11:
    not_yet_applicable) and a `combination` note (§9.1: one applicable route is
    enough).
  - The notice states that all expected outputs are relative to the 2024-08-01
    corpus snapshot.
- `tests/test_e2e_cases.py`:
  - rejects relative time words in any description;
  - checks N08's per-route statuses against the rule dates.
- `docs/phase5_design.md` (round 3):
  - pinned clock (2026-10-25) and the corpus-snapshot statement in §1;
  - Q1 and Q2 recorded as decided, with the plain G1 statement and the G4
    brittleness note;
  - the §8 table updated, with the N08 per-route table;
  - §9 records the decisions.
- `README.md`: a "Legal text version" section (frozen 2024-08-01 snapshot, fixed
  evaluation dates).
- Earlier today:
  - the round-1 and round-2 design notes;
  - the Q1 pre-check;
  - N06 and N11 reworked to be decidable from the corpus;
  - the dated retrieval note in SYSTEM_SPEC §18 and in the README.

## Open items

- The user's final look at the §8 table and the thresholds (the next step).
- Not built yet:
  - the end-to-end runner, with the pinned clock constant;
  - the attribution replay;
  - the stability and held-out guards;
  - the generalised spend tracker (`phase5_spend.jsonl`, $8 cap, $1.10 stability
    sub-cap, $3.48 held-out reserve).
- Not written yet: the SYSTEM_SPEC §18 Phase 5 pre-registration (binding text).
- Not pinned yet: the SHA of `e2e_cases.json` and of
  `determination_heldout_5_1_c.json`.
- Later in Phase 5:
  - the demo examples and their recording (after the held-out pass);
  - the full README (architecture, rejected alternatives, limitations, setup with the
    user's own key).

## Next steps, in order

1. Show the user the updated §8 table and the thresholds again.
2. After the user's OK, freeze: pin the SHAs of `e2e_cases.json` and
   `determination_heldout_5_1_c.json` in `tests/test_e2e_cases.py`.
3. Write the runner and the spend changes ($0, with tests and no API calls):
   - pinned clock 2026-10-25;
   - the stage chain from design §1;
   - the attribution replay;
   - the rounds order;
   - `HeldoutAlreadyRun`-style guards;
   - the caps and reserve.
4. Write the SYSTEM_SPEC §18 Phase 5 pre-registration from the design note: G1–G4
   with the counts, Wilson reporting, G4 brittleness, the plain D32/H07/H08
   statement, the hard rule and the budget.
5. Commit, then ask the user before pushing the branch.
6. Only after that: the stability check, then the held-out pass, each with the
   user's go-ahead.

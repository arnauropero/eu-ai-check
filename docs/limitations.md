# Limitations

The full list. The [README](../README.md) shows the four that matter most. Results are in [evaluation.md](evaluation.md); the demo examples cited below (X01–X05) are in [demo.md](demo.md).


- **It is over-cautious on social scoring (Article 5(1)(c)).**
  - On cases that touch social scoring, it often answers `unresolved` where a lawyer
    would give an answer.
  - In the held-out pass this caused 6 of the 13 missed judgements: the
    prohibited-practice determination of N01, N02, N10, N12, H02 and H04.
  - This is the limitation accepted in Phase 3.
  - The other 7 missed judgements came from extraction. They also went to
    `unresolved`.
- **A prohibited practice does not suppress the high-risk obligations.**
  - When a prohibited practice applies, the risk tier and Articles 16 and 26 are
    still determined and shown as applying.
  - In the held-out pass, H06 came out prohibited under Article 5(1)(c). Its answer
    still says "Article 26: applies to this case" and lists the Article 26
    obligations, with nothing flagging the inconsistency.
  - The Phase 4 raters reported the same thing (SYSTEM_SPEC.md §18). It comes from
    the shared determinations and renderer, not from the generator.
  - **Partly fixed after the evaluation (2026-11-11).** The answer now opens with a
    notice that the practice is prohibited and may not be carried out, and marks
    the high-risk sections "for reference only". The statuses are unchanged. This
    is a presentation change made after the held-out pass, so it is not evaluated
    (SYSTEM_SPEC.md §18). Demo example X04 shows it.
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
- **The questions in "What we would need to know" can be generic.**
  - They are written per provision, not per case.
  - In demo example X02, the first question is "Is the AI system intended to be used
    as described in this Annex III item?", followed by five Annex III items. Some
    entries only say that a determination "depends on the risk tier, which is
    itself unresolved".
- **Verbatim obligation lists include paragraphs that do not fit the case.**
  - When Article 16 or 26 applies, every paragraph of the article is quoted.
  - For the language school in demo example X01, the Article 26 list includes
    Article 26(10), on post-remote biometric identification in criminal
    investigations, and Article 26(8), on public authorities.
- **Explanations of Article 6(3) are often boilerplate.**
  - The explanation of the profiling rule tends to restate the provision rather than
    apply it to the case.
  - For example, X01 says "Article 6(3), third subparagraph, makes significant harm,
    material influence over decisions and profiling relevant to the treatment of
    listed systems", and X04 says something similar.
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
- **G2 mixes output quality with infrastructure noise.** The held-out pass made 70
  recorded call attempts: 59 completed and 11 transient connection errors, all
  recovered by the pre-registered retries. G2 passing depended on that recovery as
  well as on answer quality. The errors may be specific to the machine and network
  used for the run.
- **The new cases are not independent of the system's builder.** N01–N12 were
  written by the project author, with AI assistance, and no independent annotator
  reviewed their labels. A misreading of the Act shared by the builders and the labels would
  not show up as a miss.
- **The G3 thresholds come from a different setting.** They are development
  performance minus 0.05, measured with gold facts in Phase 3. The end-to-end run
  also includes extraction error.
- **The intervals are wide.** With 24, 36 and 10 judgements, the gates detect gross
  failure. They do not estimate accuracy precisely. For example, 20/24 is
  compatible with a true rate anywhere from 0.64 to 0.93.
- **Retrieval is not on the product path.** See "Pipeline as built" in [evaluation.md](evaluation.md).
- **The legal text is frozen.** The corpus is the 2024-08-01 text, with no
  amendments (see "Legal text version" in [evaluation.md](evaluation.md)).
  Regulation (EU) 2026/1744 (Digital Omnibus on AI), in force since 27 July 2026,
  is not included. Among other changes, it moves the high-risk rules for Annex III
  systems, including Articles 16 and 26, to 2 December 2027 (Annex I systems:
  2 August 2028) and adds two prohibitions from 2 December 2026. For dates after
  27 July 2026, answers about when high-risk obligations apply follow the 2024 text,
  and `assess` adds a one-line note saying so.
- **Phase 3 acceptance was NOT MET.** The Article 5(1)(c) shortfall was accepted as
  a documented limitation. V1 will never claim that every phase met its
  acceptance criteria.
- **Only `gpt-6.1-sol` was evaluated** as the model for extraction, matching and
  generation. The generation-model comparison was out of scope.

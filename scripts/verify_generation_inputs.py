"""Phase 4 input check (docs/phase4_design.md §0, item 5). No API call.

For every cached M-low record of the official Phase 3 run:
1. replay its recorded verdicts through `Pipeline` and require the record to be
   reproduced exactly (as scripts/retry_determination_failed.py does);
2. for every provision the adapter would cite, derive `versioned_provision_id` and
   the excerpt from the corpus and the case's relevant date alone, and require
   exactly one text version valid on that date;
3. cross-check that the replayed `ProvisionStatus.versioned_provision_id` agrees.

Prints every record/provision that cannot be derived, and a size estimate of the
cited excerpts (chars / 4 as a rough token count) for the cost estimate.
"""
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

from eu_ai_check.cli import DEFAULT_DETERMINATION_CASES, DEFAULT_OUT
from eu_ai_check.corpus.models import Corpus
from eu_ai_check.determination.combine import ARTICLE_6_1_B, OVERRIDE_ID, ROUTE_PROVISION
from eu_ai_check.determination.logic import APPLICABLE, NA_NOT_YET
from eu_ai_check.determination.evaluation import Run, load_cases, relevant_date
from eu_ai_check.determination.matcher import INSUFFICIENT, MATCHES
from eu_ai_check.determination.pipeline import OBLIGATION_GROUPS, Pipeline
from eu_ai_check.extraction.schema import CaseFacts

sys.path.insert(0, str(Path(__file__).parent))
from retry_determination_failed import Replay, norm  # noqa: E402

RUN = Path("data/eval/results/determination_v1_41c_retry.runs.jsonl")


def cited_ids(rec: dict, det) -> set[str]:
    """Every provision the adapter would put in an EvidenceItem for this record."""
    ids: set[str] = set()
    for pid, v in (rec["verdicts"] or {}).items():
        if v["verdict"] in (MATCHES, INSUFFICIENT):
            ids.add(pid)
    if rec["verdicts"]:
        ids |= set(ROUTE_PROVISION.values()) | {OVERRIDE_ID, ARTICLE_6_1_B}
    if det.roles is not None:
        for refs in det.roles.evidence.values():
            ids |= set(refs)
    for group in OBLIGATION_GROUPS:
        ids |= {s.logical_provision_id for s in det.obligations[group]}
    return ids


def main() -> None:
    corpus = Corpus.load(DEFAULT_OUT)
    pipeline = Pipeline(corpus.provisions, corpus.applicability_rules)
    versions: dict[str, list] = {}
    for p in corpus.provisions:
        versions.setdefault(p.logical_provision_id, []).append(p)
    by_id = {c["id"]: c for c in load_cases(DEFAULT_DETERMINATION_CASES)}
    recs = [json.loads(l) for l in RUN.read_text(encoding="utf-8").splitlines() if l]
    recs = [r for r in recs if r["arm"] == "matcher-low"]

    failures: list[str] = []
    sizes: list[int] = []
    lean: list[int] = []  # obligation text only where the article applies / applies later
    outcome = Counter()
    for r in recs:
        case = by_id[r["case"]]
        on = relevant_date(case)
        det = pipeline.determine(CaseFacts.model_validate(case["gold_facts"]),
                                 case["description"], on, Replay(r))
        if norm(Run(r["case"], r["repeat"], det).record(r["arm"])) != norm(r):
            failures.append(f"{r['case']}#{r['repeat']}: replay does not reproduce record")
            continue
        chars = lean_chars = 0
        quiet = {s.logical_provision_id for g in OBLIGATION_GROUPS
                 if det.group_status(g) not in (APPLICABLE, NA_NOT_YET)
                 for s in det.obligations[g]}
        for pid in sorted(cited_ids(r, det)):
            valid = [p for p in versions.get(pid, []) if p.text_valid_on(on)]
            if len(valid) != 1:
                failures.append(f"{r['case']}#{r['repeat']} ({on}): {pid} has "
                                f"{len(valid)} versions valid on that date")
                outcome["underivable"] += 1
                continue
            outcome["derived"] += 1
            chars += len(valid[0].text)
            if pid not in quiet:
                lean_chars += len(valid[0].text)
        for group in OBLIGATION_GROUPS:
            for s in det.obligations[group]:
                valid = [p for p in versions[s.logical_provision_id] if p.text_valid_on(on)]
                expected = valid[0].versioned_provision_id if len(valid) == 1 else None
                if s.versioned_provision_id != expected:
                    failures.append(f"{r['case']}#{r['repeat']}: {s.logical_provision_id} "
                                    f"pipeline {s.versioned_provision_id} != corpus {expected}")
        sizes.append(chars)
        lean.append(lean_chars)

    print(f"records: {len(recs)}; provision lookups: {dict(outcome)}")
    print(f"cited excerpt size per record, chars: median {statistics.median(sizes):.0f}, "
          f"max {max(sizes)} (~{max(sizes) // 4} tokens)")
    print(f"  obligation text only for applicable / not-yet articles: median "
          f"{statistics.median(lean):.0f}, max {max(lean)} (~{max(lean) // 4} tokens)")
    by_record = Counter(f.split(" ")[0].split(":")[0] for f in failures)
    print(f"not derivable: {len(failures)} lookups in {len(by_record)} records")
    for rec, n in sorted(by_record.items()):
        print(f"  {rec}: {n}")
    for f in failures:
        if "versions valid" not in f:
            print("  " + f)


if __name__ == "__main__":
    main()

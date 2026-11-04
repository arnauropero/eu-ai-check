"""Phase 5 end-to-end runner (SYSTEM_SPEC.md §18, Phase 5 pre-registration;
design: docs/phase5_design.md).

One case is one free-text description plus its fixed `assessment_date`. The runner
chains the shipped configuration of every stage, with nothing gold in between:

    OpenAIExtractor (gpt-6.1-sol, low, baseline prompt)
      -> Case.from_facts (the case's own assessment_date)
      -> Pipeline.determine + OpenAIMatcher (M-low, the pre-registered prompt)
      -> adapt
      -> OpenAIGenerator (prompt v2, low, 5000 output tokens) + C0-C8 + template fallback

**Pinned clock.** `EVAL_CLOCK` is a constant. Nothing on this path reads the system
clock: a case without an assessment date gets `EVAL_CLOCK`, and no case may be
dated after it. A test runs the whole path with the clock functions disabled.

**API failures** follow the Phase 4 policy: up to 3 retries for transient errors
(connection, timeout, HTTP 5xx), each one guarded, billed and recorded; HTTP 429 and
other 4xx fail at once; content failures are never retried; the client is built
with `max_retries=0`. A case whose stage still fails ships no answer, and all its
judgements are scored as wrong. It is not re-run.

**Two experiments, never pooled:**
- `run_stability`: D32, D10, D35 and D39 × 3 on dev data, in rounds, under the $1.50
  cap. Reported only. Its first run is the wiring smoke test.
- `run_heldout`: N01-N12 + H01-H08, each run exactly once (`HeldoutAlreadyRun`).
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Sequence

from ..corpus.models import Corpus, Provision
from ..determination import matcher as matcher_mod
from ..determination.matcher import MatchResult, OpenAIMatcher
from ..determination.pipeline import OBLIGATION_GROUPS, Determination, Pipeline
from ..extraction import extractor as extractor_mod
from ..extraction.extractor import ExtractionResult
from ..extraction.openai_extractor import OpenAIExtractor
from ..extraction.schema import Case, CaseFacts
from ..generation import evaluation as gen_eval
from ..generation.adapter import AdapterError, GenerationInput, adapt
from ..generation.generator import PROMPT_VERSION, OpenAIGenerator, prompt_sha
from ..generation.inputs import Item
from .scoring import score_heldout, score_stability
from .spend import BudgetExceeded, E2ESpend

#: The pinned evaluation clock (decided 2026-10-25). Never the wall clock.
EVAL_CLOCK = date(2026, 10, 25)
#: The frozen corpus snapshot every expected output is relative to.
CORPUS_SNAPSHOT = date(2024, 8, 1)

CORPUS = Path("data/corpus")
E2E_CASES = Path("data/eval/e2e_cases.json")
HELDOUT_5_1_C = Path("data/eval/determination_heldout_5_1_c.json")
DEV_CASES = Path("data/eval/determination_cases.json")
RESULTS = Path("data/eval/results")
HELDOUT_MARKER = RESULTS / "e2e_heldout.started"
HELDOUT_RUNS = RESULTS / "e2e_heldout.runs.jsonl"
HELDOUT_SUMMARY = RESULTS / "e2e_heldout.json"

STABILITY_CASES = ("D32", "D10", "D35", "D39")
STABILITY_REPEATS = 3

#: Frozen stage configurations (Phases 2-4, adopted).
MODEL = "gpt-6.1-sol"
EXTRACTION_EFFORT = "low"
EXTRACTION_PROMPT = "baseline"
MATCHER_EFFORT = "low"
MATCHER_PROMPT = "baseline"
GENERATION_EFFORT = "low"
GENERATION_PROMPT_VERSION = "v2"
GENERATION_PROMPT_SHA = "a6b4a491da53588b40fd575d34756d0432068fb9f87d7bbc4313cdd68aece0f6"
GENERATION_MAX_OUTPUT_TOKENS = 5000

MAX_TRANSIENT_RETRIES = gen_eval.MAX_TRANSIENT_RETRIES
RETRY_DELAYS_S = gen_eval.RETRY_DELAYS_S


class HeldoutAlreadyRun(RuntimeError):
    pass


class StabilityAlreadyRun(RuntimeError):
    pass


class StageFailed(RuntimeError):
    """A stage's API call still failed after the retry policy."""

    def __init__(self, stage: str, error: str):
        super().__init__(f"{stage}: {error}")
        self.stage, self.error = stage, error


# --------------------------------------------------------------------------
# Clients and stages
# --------------------------------------------------------------------------

class RecordingClient:
    """Wraps an OpenAI client and keeps the last response, so the ledger can carry
    the API's own response id and `created_at` for every stage."""

    def __init__(self, inner: Any):
        self._inner = inner
        self.last: Any = None
        self.responses = self

    def create(self, **request: Any) -> Any:
        self.last = None
        self.last = self._inner.responses.create(**request)
        return self.last


def default_client() -> RecordingClient:
    import openai

    # No hidden SDK retries: every retry goes through the runner (§18 policy).
    return RecordingClient(openai.OpenAI(max_retries=0))


@dataclass
class Stages:
    client: RecordingClient
    extractor: OpenAIExtractor
    matcher: OpenAIMatcher
    generator: OpenAIGenerator

    @classmethod
    def frozen(cls, client: RecordingClient) -> Stages:
        return cls(client,
                   OpenAIExtractor(client, model=MODEL, effort=EXTRACTION_EFFORT,
                                   prompt_variant=EXTRACTION_PROMPT),
                   OpenAIMatcher(client, model=MODEL, effort=MATCHER_EFFORT,
                                 prompt_variant=MATCHER_PROMPT),
                   OpenAIGenerator(client, model=MODEL, effort=GENERATION_EFFORT,
                                   max_output_tokens=GENERATION_MAX_OUTPUT_TOKENS))

    def check_frozen(self) -> None:
        """Refuse to run anything but the adopted configuration."""
        x, m, g = self.extractor, self.matcher, self.generator
        problems = [
            *(["extractor"] if (x.model, x.effort, x.prompt_variant)
              != (MODEL, EXTRACTION_EFFORT, EXTRACTION_PROMPT) else []),
            *(["matcher"] if (m.model, m.effort, m.prompt_variant)
              != (MODEL, MATCHER_EFFORT, MATCHER_PROMPT) else []),
            *(["generator"] if (g.model, g.effort, g.max_output_tokens)
              != (MODEL, GENERATION_EFFORT, GENERATION_MAX_OUTPUT_TOKENS) else []),
            *(["generation prompt"] if (PROMPT_VERSION, prompt_sha())
              != (GENERATION_PROMPT_VERSION, GENERATION_PROMPT_SHA) else []),
        ]
        if problems:
            raise RuntimeError(f"not the frozen configuration: {problems}")


def _request_chars(request: dict[str, Any]) -> int:
    return sum(len(m["content"]) for m in request["input"])


def _audit(client: RecordingClient) -> dict[str, Any]:
    last = client.last
    return {"response_id": getattr(last, "id", None),
            "api_created_at": getattr(last, "created_at", None)}


def _guarded(tracker: E2ESpend, phase: str, stage: str, ref: str, client: RecordingClient,
             request: dict[str, Any], call, model: str):
    """One stage call under the API-failure policy. Returns the stage result, or
    raises StageFailed. BudgetExceeded propagates (hard stop)."""
    for attempt in range(1 + MAX_TRANSIENT_RETRIES):
        tracker.guard(phase, stage, _request_chars(request), request["max_output_tokens"])
        try:
            result = call()
        except Exception as exc:  # transport / API error: recorded, maybe retried
            error = f"{type(exc).__name__}: {str(exc)[:160]}"
            transient = gen_eval.is_transient(exc)
            tracker.record(phase, stage, ref, model, 0, 0, error=error, attempt=attempt + 1,
                           transient=transient, **_audit(client))
            if transient and attempt < MAX_TRANSIENT_RETRIES:
                time.sleep(RETRY_DELAYS_S[attempt])
                continue
            raise StageFailed(stage, error) from exc
        tracker.record(phase, stage, ref, result.served_model or model, result.input_tokens,
                       result.output_tokens, error=result.error, attempt=attempt + 1,
                       **_audit(client))
        return result
    raise AssertionError("unreachable")  # pragma: no cover


class GuardedMatcher:
    """The frozen matcher behind the spend guard and the retry policy. `Pipeline`
    calls it only when some candidate is in force (none before entry into force)."""

    def __init__(self, inner: OpenAIMatcher, tracker: E2ESpend, phase: str, ref: str,
                 client: RecordingClient):
        self.inner, self.tracker, self.phase, self.ref, self.client = (
            inner, tracker, phase, ref, client)
        self.name = inner.name
        self.calls = 0

    def match(self, description: str, candidates: dict[str, list[Provision]]) -> MatchResult:
        self.calls += 1
        request = self.inner.request(description, candidates)
        return _guarded(self.tracker, self.phase, "match", self.ref, self.client, request,
                        lambda: self.inner.match(description, candidates), self.inner.model)


# --------------------------------------------------------------------------
# One case, end to end
# --------------------------------------------------------------------------

@dataclass
class CaseRun:
    case_id: str
    stratum: str                         # "A" (N01-N12), "B" (H01-H08), "dev" (stability)
    repeat: int
    assessment_date: str
    stage_failed: str | None = None      # extract | match | adapt | generate | budget
    error: str | None = None
    extraction: dict[str, Any] = field(default_factory=dict)
    relevant_date: str | None = None
    relevant_date_basis: str | None = None
    predicted: dict[str, Any] = field(default_factory=dict)
    risk_tier_detail: dict[str, Any] = field(default_factory=dict)
    adapter_statuses: dict[str, str] = field(default_factory=dict)
    verdicts: dict[str, Any] = field(default_factory=dict)
    matcher_error: str | None = None
    generation: dict[str, Any] | None = None
    # In memory only, for scoring and the attribution replay.
    facts: CaseFacts | None = field(default=None, repr=False)
    determination: Determination | None = field(default=None, repr=False)
    gen_input: GenerationInput | None = field(default=None, repr=False)

    def record(self) -> dict[str, Any]:
        out = asdict(self)
        for k in ("facts", "determination", "gen_input"):
            out.pop(k)
        return out


def assessment_date(case: dict) -> date:
    on = date.fromisoformat(case["assessment_date"]) if case.get("assessment_date") else EVAL_CLOCK
    if on > EVAL_CLOCK:
        raise ValueError(f"{case['id']}: assessment_date {on} is after the pinned clock {EVAL_CLOCK}")
    return on


def predicted_labels(det: Determination) -> dict[str, Any]:
    return {"risk_tier": det.risk_tier.status, "prohibited_practice": det.prohibited.status,
            "actor_role": det.role_label(),
            **{g: det.group_status(g) for g in OBLIGATION_GROUPS}}


def _adapter_statuses(inp: GenerationInput) -> dict[str, str]:
    return {d.determination_id: d.status_key for d in inp.determinations}


def run_case(case: dict, stratum: str, repeat: int, phase: str, stages: Stages,
             tracker: E2ESpend, corpus: Corpus, pipeline: Pipeline,
             corpus_texts: dict[str, str]) -> CaseRun:
    on_assess = assessment_date(case)
    ref = f"{case['id']}#{repeat}"
    run = CaseRun(case["id"], stratum, repeat, on_assess.isoformat())
    desc = case["description"]
    client = stages.client

    # 1. Extraction.
    x = stages.extractor
    try:
        ext: ExtractionResult = _guarded(tracker, phase, "extract", ref, client,
                                         x.request(desc, on_assess),
                                         lambda: x.extract(desc, on_assess), x.model)
    except StageFailed as exc:
        run.stage_failed, run.error = exc.stage, exc.error
        return run
    run.extraction = {"facts": ext.raw if ext.ok else None, "error": ext.error,
                      "leaks": [asdict(l) for l in ext.leaks],
                      "served_model": ext.served_model, "effort": ext.effort,
                      "prompt_variant": ext.prompt_variant,
                      "input_tokens": ext.input_tokens, "output_tokens": ext.output_tokens}
    if not ext.ok:
        run.stage_failed, run.error = "extract", ext.error
        return run
    run.facts = ext.facts

    # 2. Case: the case's own assessment date, never the wall clock.
    c = Case.from_facts(ext.facts, desc, on_assess)
    on = c.relevant_date
    run.relevant_date, run.relevant_date_basis = on.isoformat(), c.relevant_date_basis.value

    # 3. Determination.
    guarded = GuardedMatcher(stages.matcher, tracker, phase, ref, client)
    try:
        det = pipeline.determine(ext.facts, desc, on, guarded)
    except StageFailed as exc:
        run.stage_failed, run.error = exc.stage, exc.error
        return run
    run.determination = det
    run.predicted = predicted_labels(det)
    rt = det.risk_tier
    run.risk_tier_detail = {"routes": rt.routes, "system_category": rt.system_category,
                            "derogation": rt.derogation, "matched": rt.matched,
                            "prohibited_points": det.prohibited.points}
    if det.match:
        run.matcher_error = det.match.error
        run.verdicts = {pid: {"verdict": v.verdict, "model_verdict": v.model_verdict,
                              "downgrade": v.downgrade_reason, "case_quote": v.case_quote,
                              "provision_quote": v.provision_quote, "rationale": v.rationale}
                        for pid, v in det.match.verdicts.items()}

    # 4. Adapter.
    try:
        inp = adapt(det, ext.facts, on, corpus, pipeline, ref)
    except AdapterError as exc:
        run.stage_failed, run.error = "adapt", f"AdapterError: {exc}"
        return run
    run.gen_input = inp
    run.adapter_statuses = _adapter_statuses(inp)

    # 5. Generation: the Phase 4 harness, unchanged, on the Phase 5 ledger.
    item = Item(ref, inp, synthetic=False, gold_differs=False)
    result = gen_eval.evaluate_item(item, stages.generator, tracker.view("generate", client),
                                    phase, corpus_texts)
    run.generation = asdict(result)
    return run


# --------------------------------------------------------------------------
# Loading and SHAs
# --------------------------------------------------------------------------

def content_sha(cases: list[dict]) -> str:
    return hashlib.sha256(json.dumps(cases, sort_keys=True, ensure_ascii=False)
                          .encode("utf-8")).hexdigest()


def load(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["cases"]


def heldout_cases() -> list[tuple[str, dict]]:
    return [("A", c) for c in load(E2E_CASES)] + [("B", c) for c in load(HELDOUT_5_1_C)]


def _meta(stages: Stages) -> dict[str, Any]:
    return {
        "eval_clock": EVAL_CLOCK.isoformat(), "corpus_snapshot": CORPUS_SNAPSHOT.isoformat(),
        "e2e_cases_sha256": content_sha(load(E2E_CASES)),
        "heldout_5_1_c_sha256": content_sha(load(HELDOUT_5_1_C)),
        "extractor": {"model": stages.extractor.model, "effort": stages.extractor.effort,
                      "prompt_variant": stages.extractor.prompt_variant,
                      "system_prompt_sha256": hashlib.sha256(
                          extractor_mod.system_prompt(EXTRACTION_PROMPT).encode()).hexdigest()},
        "matcher": {"model": stages.matcher.model, "effort": stages.matcher.effort,
                    "prompt_variant": stages.matcher.prompt_variant,
                    "system_prompt_sha256": hashlib.sha256(
                        matcher_mod.system_prompt(MATCHER_PROMPT).encode()).hexdigest()},
        "generator": {"model": stages.generator.model, "effort": stages.generator.effort,
                      "max_output_tokens": stages.generator.max_output_tokens,
                      "prompt_version": PROMPT_VERSION, "prompt_sha": prompt_sha()},
    }


def _write_jsonl(path: Path, rows: Sequence[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


class _Env:
    def __init__(self, corpus_dir: Path = CORPUS):
        self.corpus = Corpus.load(corpus_dir)
        self.pipeline = Pipeline(self.corpus.provisions, self.corpus.applicability_rules)
        self.texts = {p.versioned_provision_id: p.text for p in self.corpus.provisions}


# --------------------------------------------------------------------------
# Stability experiment (dev data, reported only)
# --------------------------------------------------------------------------

def run_stability(stages: Stages, tracker: E2ESpend, *, label: str = "v1",
                  results_dir: Path = RESULTS) -> dict[str, Any]:
    stages.check_frozen()
    runs_path = results_dir / f"e2e_stability_{label}.runs.jsonl"
    summary_path = results_dir / f"e2e_stability_{label}.json"
    if runs_path.exists() or summary_path.exists():
        raise StabilityAlreadyRun(f"{summary_path} exists; use a new label")
    if tracker.calls("heldout"):
        raise RuntimeError("the stability check runs before the held-out pass")
    env = _Env()
    dev = {c["id"]: c for c in load(DEV_CASES)}
    cases = [dev[cid] for cid in STABILITY_CASES]
    runs: list[CaseRun] = []
    stopped = None
    smoke_failed = None
    for repeat in range(STABILITY_REPEATS):          # rounds: repeat r of every case
        for case in cases:
            try:
                run = run_case(case, "dev", repeat, "stability", stages, tracker,
                               env.corpus, env.pipeline, env.texts)
            except BudgetExceeded as exc:
                stopped = str(exc)
                break
            runs.append(run)
            if len(runs) == 1 and run.stage_failed:   # the wiring smoke test
                smoke_failed = f"{run.stage_failed}: {run.error}"
                break
        if stopped or smoke_failed:
            break
    summary = {**score_stability(runs, {c["id"]: c for c in cases}, env.pipeline),
               "stopped_by_budget": stopped, "smoke_test_failed": smoke_failed,
               "spent_stability_usd": round(tracker.spent_in("stability"), 6),
               "spent_total_usd": round(tracker.spent, 6), **_meta(stages)}
    _write_jsonl(runs_path, [r.record() for r in runs])
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8")
    return summary


# --------------------------------------------------------------------------
# The single held-out pass
# --------------------------------------------------------------------------

def run_heldout(stages: Stages, tracker: E2ESpend, *, confirm: bool,
                results_dir: Path = RESULTS) -> dict[str, Any]:
    """Exactly once. A marker is written before the first call, so even a crashed
    pass cannot be re-run."""
    stages.check_frozen()
    marker = results_dir / HELDOUT_MARKER.name
    runs_path, summary_path = results_dir / HELDOUT_RUNS.name, results_dir / HELDOUT_SUMMARY.name
    if not confirm:
        raise HeldoutAlreadyRun("the held-out pass needs explicit confirmation (--confirm-heldout)")
    if marker.exists() or runs_path.exists() or summary_path.exists() or tracker.calls("heldout"):
        raise HeldoutAlreadyRun("the held-out pass has already been started; it runs exactly once")
    cases = heldout_cases()
    if [c["id"] for _, c in cases] != [f"N{i:02d}" for i in range(1, 13)] + [
            f"H{i:02d}" for i in range(1, 9)]:
        raise RuntimeError("the held-out set must be exactly N01-N12 + H01-H08")
    tracker.preflight_heldout()
    env = _Env()
    results_dir.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps(_meta(stages)) + "\n", encoding="utf-8")

    runs: list[CaseRun] = []
    budget_stop = None
    for stratum, case in cases:
        if budget_stop:
            runs.append(CaseRun(case["id"], stratum, 0, case["assessment_date"], "budget",
                                f"not called: {budget_stop}"))
            continue
        try:
            runs.append(run_case(case, stratum, 0, "heldout", stages, tracker, env.corpus,
                                 env.pipeline, env.texts))
        except BudgetExceeded as exc:  # should be impossible with the reserve
            budget_stop = str(exc)
            runs.append(CaseRun(case["id"], stratum, 0, case["assessment_date"], "budget",
                                f"not called: {budget_stop}"))
    summary = {**score_heldout(runs, {c["id"]: c for _, c in cases}, env.pipeline),
               "budget_stop": budget_stop, "spent_heldout_usd": round(tracker.spent_in("heldout"), 6),
               "spent_total_usd": round(tracker.spent, 6), **_meta(stages)}
    _write_jsonl(runs_path, [r.record() for r in runs])
    summary_path.write_text(json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8")
    return summary

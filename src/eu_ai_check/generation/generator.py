"""The claims-first LLM generator (SYSTEM_SPEC.md §16.3). Model configurable.

The model receives only `GenerationInput.as_prompt_data()` (invariant 23) and
returns strict JSON claims. It has no way to set a status (invariant 24): status
lines, citations and missing-information lists are rendered by code. Anything
that is not a schema-valid answer comes back as `answer=None` with an error, and
check C0 then fails it. Transport errors from the client propagate to the caller,
which records them (the harness counts them as failures in the held-out pass).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from .adapter import GenerationInput
from .schema import SCHEMA_NAME, GeneratedAnswer, parse_answer, response_schema

EFFORTS = ("low", "medium", "high")
#: Caps reasoning + visible output together (OpenAI reasoning guide). 5000 makes the
#: held-out worst case ($2.39) fit beside the dev plan; recorded as a dated amendment
#: in SYSTEM_SPEC §18. A call that hits it finishes "incomplete:max_output_tokens".
MAX_OUTPUT_TOKENS = 5000

SYSTEM_PROMPT = """\
You write the explanatory claims of an EU AI Act (Regulation (EU) 2024/1689) compliance
analysis of the user's AI system. Every determination in the input is final. Each
determination's status line, its sources and, for an unresolved determination, the
list of missing information are printed separately. You add short claims that explain
the determinations, grounded only in the evidence given. You never decide, change,
restate or question a status.

Input: a JSON object with "determinations". Each has a determination_id, a type, an
applicability_status (applicable / not_applicable / unresolved / not_in_force), a
not_applicable_reason, an analysis (a short summary of the findings for the user's
system), its evidence (evidence_id, citation and, for some items, the provision's text)
and, when unresolved, the unresolved_inputs (the question that could not be answered,
sometimes with a rationale).

Output: {"claims": [...]}. Each claim has:
- claim_id: "c1", "c2", ... unique.
- determination_id: the one determination the claim explains.
- text: one or two plain-language sentences.
- evidence_ids: one or more evidence_ids taken only from that same determination's
  evidence. Never cite another determination's evidence.
- quote: null, or a short span copied character for character from the text of one
  cited evidence item; then quote_evidence_id is that item's evidence_id. Otherwise
  both are null. Never quote an item that has no text.

What to write:
- Write at least one claim for every determination that has evidence and is either
  unresolved, or is the risk tier or prohibited practice determination, or is an
  obligation article that is applicable or not yet applicable. Other claims are
  optional. Write no claim for a determination that has no evidence.
- Explain what the cited provisions say and how the analysis connects them to the
  user's system. For an applicable obligation article, summarise what its provisions
  require, citing the provisions you summarise.
- Write about the law and the user's system, never about how the analysis was produced:
  do not mention code, software, records, recording, matching, the input, or an
  assessment carried out by the analysis.
- Some evidence items are citations without text. Cite them as usual, but do not
  mention excerpts, supplied evidence or the absence of text.
- For an unresolved determination, explain why each missing piece of information
  matters to the cited provision. Do not guess the answer and do not suggest which
  way it would go.
- Use only the input. Do not add facts about the user or the system that are not in
  the input, and do not cite or name any provision that is not in the claim's own
  evidence: every "Article ..." or "Annex ..." you mention must be one of the claim's
  cited items.

Words you must not use in any claim, because the status line already says it and
your wording must never contradict it: "apply", "applies", "applicable",
"not applicable", "exempt", "excluded", "out of scope", "not covered", "subject to",
"high-risk", "not high-risk", "prohibited", "in force", "unresolved",
"undetermined", "may", "might", "possibly", "error", "failed", "unable to",
"you must", "you are required", "qualifies as", "you are a provider",
"you are a deployer". Use "must", "shall" or "is required to" only in a claim that
cites an obligation provision of that determination.
"""


#: Every prompt change in dev gets a label and a one-line note (what and why). The
#: ledger records the label, `prompt_sha()` and the note with every call, plus the
#: SHA of the exact request sent. v0 was used only for the smoke test (its ledger
#: line predates these fields).
PROMPT_VERSION = "v2"
PROMPT_HISTORY = {
    "v0": "pre-registered system prompt and schema (364293b); smoke test only",
    "v1": "system prompt and schema unchanged from v0; adapter explanations reworded "
          "without internal ids or None (smoke showed them echoed as 'The code identifies')",
    "v2": "system prompt and input labels describe an analysis of the user's system (no "
          "'code'), citation-only items not to be discussed, derogation 'not_assessed' "
          "worded as not reached; dev pass 1 had 70/179 claims with system language "
          "(diff: data/eval/prompts/generator_v1_to_v2.diff)",
}
#: The committed diff of what the model sees, per version after v1 (ledger: prompt_diff).
PROMPT_DIFFS = {"v2": "data/eval/prompts/generator_v1_to_v2.diff"}


def prompt_sha() -> str:
    payload = json.dumps({"system": SYSTEM_PROMPT, "schema": response_schema()},
                         sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def request_sha(request: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(request, sort_keys=True, ensure_ascii=False)
                          .encode("utf-8")).hexdigest()


def user_message(inp: GenerationInput) -> str:
    return json.dumps(inp.as_prompt_data(), ensure_ascii=False, indent=1)


@dataclass
class GenerationResult:
    answer: GeneratedAnswer | None
    raw: str | None
    error: str | None
    served_model: str | None
    input_tokens: int
    output_tokens: int              # includes reasoning tokens (billed as output)
    reasoning_tokens: int | None = None
    #: "completed" | "incomplete:<reason>" (e.g. incomplete:max_output_tokens) |
    #: "refusal" | "status:<status>". Truncation is reported apart from bad content.
    finish_reason: str | None = None
    response_id: str | None = None
    #: The API's usage object as returned (model_dump), for auditing token fields.
    usage_raw: dict | None = None
    request_sha: str | None = None


class OpenAIGenerator:
    """`model` is required: the generator hard-codes no model (Phase 5 compares
    models). The pre-registered Phase 4 choice lives in the harness."""

    def __init__(self, client: Any = None, *, model: str, effort: str = "low",
                 max_output_tokens: int = MAX_OUTPUT_TOKENS):
        if not model:
            raise ValueError("a generation model must be named")
        if effort not in EFFORTS:
            raise ValueError(f"unknown effort {effort!r} ({EFFORTS})")
        if client is None:
            import openai

            # No hidden SDK retries: every retry goes through the harness, which
            # guards, bills and records it (§18 API-failure policy).
            client = openai.OpenAI(max_retries=0)
        self.client = client
        self.model = model
        self.effort = effort
        self.max_output_tokens = max_output_tokens
        self.name = f"generator:{model}:{effort}"

    def request(self, inp: GenerationInput) -> dict[str, Any]:
        return {
            "model": self.model,
            "input": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message(inp)},
            ],
            "reasoning": {"effort": self.effort},
            "max_output_tokens": self.max_output_tokens,
            "text": {"format": {"type": "json_schema", "name": SCHEMA_NAME,
                                "schema": response_schema(), "strict": True}},
        }

    def generate(self, inp: GenerationInput) -> GenerationResult:
        from ..extraction.openai_extractor import _message_content

        request = self.request(inp)
        response = self.client.responses.create(**request)
        usage = getattr(response, "usage", None)
        details = getattr(usage, "output_tokens_details", None)
        dump = getattr(usage, "model_dump", None)
        result = GenerationResult(
            answer=None, raw=None, error=None,
            served_model=getattr(response, "model", None),
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            reasoning_tokens=getattr(details, "reasoning_tokens", None),
            response_id=getattr(response, "id", None),
            usage_raw=dump() if callable(dump) else None,
            request_sha=request_sha(request),
        )
        refusal, text = _message_content(response)
        status = getattr(response, "status", None)
        if refusal is not None:
            result.finish_reason = "refusal"
            result.error = f"refusal: {refusal[:120]}"
        elif status == "incomplete":
            reason = getattr(getattr(response, "incomplete_details", None), "reason", None)
            result.finish_reason = f"incomplete:{reason}"
            result.error = f"incomplete response ({reason})"
        elif status not in (None, "completed"):
            result.finish_reason = f"status:{status}"
            result.error = f"response status {status}"
        else:
            result.finish_reason = "completed"
            result.raw = text
            result.answer, result.error = parse_answer(text)
        return result

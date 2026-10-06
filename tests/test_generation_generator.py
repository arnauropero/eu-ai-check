"""The LLM generator, with a mock client only. No API call is ever made: the
suite-wide guard in conftest.py fails any test that instantiates a real client."""

from __future__ import annotations

import json
from types import SimpleNamespace

import openai
import pytest
from conftest import RealClientInstantiated
from generation_fixtures import cases, corpus_texts, load_input, records

from eu_ai_check.generation.checks import run_checks
from eu_ai_check.generation.generator import (
    SYSTEM_PROMPT,
    OpenAIGenerator,
    prompt_sha,
    user_message,
)
from eu_ai_check.generation.render import render
from eu_ai_check.generation.schema import response_schema


def fake_response(text: str | None = None, *, refusal: str | None = None,
                  status: str = "completed", reason: str | None = None,
                  reasoning: int | None = 1800) -> SimpleNamespace:
    content = []
    if refusal is not None:
        content.append(SimpleNamespace(type="refusal", refusal=refusal))
    if text is not None:
        content.append(SimpleNamespace(type="output_text", text=text))
    return SimpleNamespace(
        output=[SimpleNamespace(type="message", content=content)],
        usage=SimpleNamespace(input_tokens=4000, output_tokens=2500,
                              output_tokens_details=SimpleNamespace(reasoning_tokens=reasoning)),
        status=status, model="mock-model",
        incomplete_details=SimpleNamespace(reason=reason) if reason else None)


class MockClient:
    def __init__(self, response: SimpleNamespace):
        self.calls: list[dict] = []
        self.responses = SimpleNamespace(create=self._create)
        self._response = response

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


GOOD = {"claims": [{"claim_id": "c1", "determination_id": "risk_tier",
                    "text": "Annex III(4)(a) lists AI systems used to recruit or select "
                            "natural persons.", "evidence_ids": ["E3"], "quote": None,
                    "quote_evidence_id": None}]}


@pytest.mark.expects_client_attempt
def test_guard_blocks_real_clients() -> None:
    with pytest.raises(RealClientInstantiated):
        openai.OpenAI()
    with pytest.raises(RealClientInstantiated):
        OpenAIGenerator(model="gpt-6.1-sol")


@pytest.mark.expects_client_attempt
def test_guard_fails_a_test_even_if_the_error_is_swallowed() -> None:
    """Simulates code that catches everything: the attempt is still recorded, and
    an unmarked test would fail at teardown."""
    from conftest import _attempts
    try:
        OpenAIGenerator(model="gpt-6.1-sol")
    except Exception:
        pass
    assert _attempts


def test_model_must_be_named() -> None:
    with pytest.raises(ValueError):
        OpenAIGenerator(MockClient(fake_response("{}")), model="")


def test_request_shape_and_configurable_model() -> None:
    client = MockClient(fake_response(json.dumps(GOOD)))
    gen = OpenAIGenerator(client, model="some-cheaper-model", effort="low")
    inp = load_input("D01#0")
    gen.generate(inp)
    (call,) = client.calls
    assert call["model"] == "some-cheaper-model"
    assert call["reasoning"] == {"effort": "low"}
    assert call["text"]["format"]["strict"] is True
    assert call["text"]["format"]["schema"] == response_schema()
    assert call["input"][0]["content"] == SYSTEM_PROMPT
    assert json.loads(call["input"][1]["content"]) == inp.as_prompt_data()


def test_prompt_never_contains_the_user_description() -> None:
    record = records()["D01#0"]
    message = user_message(load_input("D01#0"))
    assert cases()[record["case"]]["description"].strip()[:60] not in message


def test_good_answer_parses_and_passes_checks() -> None:
    inp = load_input("D01#0")
    assert inp.evidence()["E3"].provision_citation == "Annex III(4)(a)"
    result = OpenAIGenerator(MockClient(fake_response(json.dumps(GOOD))),
                             model="m").generate(inp)
    assert result.error is None and result.answer is not None
    assert (result.input_tokens, result.output_tokens) == (4000, 2500)
    report = run_checks(inp, render(inp, result.answer), result.answer,
                        parse_error=result.error, corpus_texts=corpus_texts())
    assert report.passed, report.failures


@pytest.mark.parametrize("response, fragment", [
    (fake_response(refusal="I can't help"), "refusal"),
    (fake_response(status="incomplete", reason="max_output_tokens"), "incomplete"),
    (fake_response(status="failed"), "status failed"),
    (fake_response("{not json"), "schema violation"),
    (fake_response(json.dumps({"claims": [{"claim_id": "c1"}]})), "schema violation"),
    (fake_response(None), "empty output"),
])
def test_bad_responses_fail_closed(response, fragment) -> None:
    result = OpenAIGenerator(MockClient(response), model="m").generate(load_input("D01#0"))
    assert result.answer is None and fragment in result.error


def test_prompt_sha_is_stable() -> None:
    assert prompt_sha() == prompt_sha() and len(prompt_sha()) == 64


def test_finish_reason_and_reasoning_tokens_are_captured() -> None:
    ok = OpenAIGenerator(MockClient(fake_response(json.dumps(GOOD))), model="m").generate(
        load_input("D01#0"))
    assert (ok.finish_reason, ok.reasoning_tokens) == ("completed", 1800)
    cut = OpenAIGenerator(MockClient(fake_response(status="incomplete", reason="max_output_tokens",
                                                   reasoning=4990)), model="m").generate(
        load_input("D01#0"))
    assert cut.finish_reason == "incomplete:max_output_tokens" and cut.reasoning_tokens == 4990
    assert cut.answer is None


def test_raw_usage_response_id_and_request_sha_are_kept() -> None:
    from eu_ai_check.generation.generator import request_sha

    response = fake_response(json.dumps(GOOD))
    raw = {"input_tokens": 4000, "output_tokens": 2500,
           "output_tokens_details": {"reasoning_tokens": 1800}}
    response.usage.model_dump = lambda: raw
    response.id = "resp_123"
    client = MockClient(response)
    gen = OpenAIGenerator(client, model="m")
    r = gen.generate(load_input("D01#0"))
    assert (r.usage_raw, r.response_id) == (raw, "resp_123")
    assert r.request_sha == request_sha(client.calls[0]) and len(r.request_sha) == 64


def test_every_prompt_version_has_a_note() -> None:
    from eu_ai_check.generation.generator import PROMPT_HISTORY, PROMPT_VERSION

    assert PROMPT_VERSION in PROMPT_HISTORY and all(n.strip() for n in PROMPT_HISTORY.values())


def test_prompt_v2_is_pinned_and_its_diff_is_committed() -> None:
    from pathlib import Path

    from eu_ai_check.generation.generator import PROMPT_DIFFS, PROMPT_VERSION

    assert PROMPT_VERSION == "v2"
    assert prompt_sha() == "a6b4a491da53588b40fd575d34756d0432068fb9f87d7bbc4313cdd68aece0f6"
    diff = Path(PROMPT_DIFFS["v2"]).read_text(encoding="utf-8")
    assert "+analysis of the user's AI system." in diff and '+   "analysis":' in diff

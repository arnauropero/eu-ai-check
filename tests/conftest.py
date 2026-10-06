"""Suite-wide guard: no test may instantiate a real OpenAI client.

Every test runs with `openai.OpenAI` and `openai.AsyncOpenAI` replaced by a
constructor that fails the test. Code under test must receive a mock client
explicitly. `tests/test_generation_generator.py` checks that the guard is active.
"""

from __future__ import annotations

import pytest


class RealClientInstantiated(AssertionError):
    pass


def _forbidden(*args, **kwargs):
    raise RealClientInstantiated("a real OpenAI client was instantiated during the test suite")


@pytest.fixture(autouse=True)
def _no_real_openai_client(monkeypatch):
    try:
        import openai
    except ImportError:  # pragma: no cover - openai is a dependency
        return
    monkeypatch.setattr(openai, "OpenAI", _forbidden)
    monkeypatch.setattr(openai, "AsyncOpenAI", _forbidden)

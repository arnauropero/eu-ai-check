"""Suite-wide guard: no test may instantiate a real OpenAI client.

Every test runs with `openai.OpenAI` and `openai.AsyncOpenAI` replaced by a
constructor that raises. Each attempt is also recorded, and the test fails at
teardown even if the code under test swallowed the exception. Code under test must
receive a mock client explicitly. `tests/test_generation_generator.py` checks that
the guard is active.
"""

from __future__ import annotations

import pytest


class RealClientInstantiated(AssertionError):
    pass


_attempts: list[str] = []


def _forbidden(*args, **kwargs):
    _attempts.append("openai client")
    raise RealClientInstantiated("a real OpenAI client was instantiated during the test suite")


@pytest.fixture(autouse=True)
def _no_real_openai_client(monkeypatch, request):
    try:
        import openai
    except ImportError:  # pragma: no cover - openai is a dependency
        yield
        return
    monkeypatch.setattr(openai, "OpenAI", _forbidden)
    monkeypatch.setattr(openai, "AsyncOpenAI", _forbidden)
    _attempts.clear()
    yield
    expected = request.node.get_closest_marker("expects_client_attempt") is not None
    attempted = bool(_attempts)
    _attempts.clear()
    if attempted and not expected:
        pytest.fail("a real OpenAI client was instantiated during this test")


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "expects_client_attempt: the test deliberately trips the client guard")

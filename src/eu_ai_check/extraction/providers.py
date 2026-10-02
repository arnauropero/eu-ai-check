"""Extraction provider selection.

**Phase 2 extraction model: OpenAI GPT-6.1 Sol (`gpt-6.1-sol`).** Switched from
Claude Opus 5.5 on 2026-10-02, after the eval set was pre-registered (`fe0b0f6`)
and before any extraction API call. The switch changes the model under test, not
the evaluation: eval set, labels, field rules, prompt, leakage check, scoring and
categories are all unchanged. The Anthropic extractor stays available as an
alternative provider.
"""

from __future__ import annotations

from typing import Any

from .extractor import ANTHROPIC_MODEL, AnthropicExtractor
from .openai_extractor import OPENAI_MODEL, OpenAIExtractor

DEFAULT_PROVIDER = "openai"
MODELS = {"openai": OPENAI_MODEL, "anthropic": ANTHROPIC_MODEL}


def create_extractor(provider: str = DEFAULT_PROVIDER, *, effort: str = "medium",
                     client: Any = None):
    if provider == "openai":
        return OpenAIExtractor(client, effort=effort)
    if provider == "anthropic":
        return AnthropicExtractor(client, effort=effort)
    raise ValueError(f"unknown extraction provider {provider!r}")

"""LLM adapter implementations.

Sub-modules
-----------
- ``openrouter`` — ``OpenRouterIntentClassifier`` that makes real HTTP
  calls to the OpenRouter API.  Falls back to a deterministic classifier
  when the API key is not configured, and fails clearly when the key is
  set but the upstream is unreachable.
- ``factory`` — ``LLMClassifierFactory`` that resolves per-tenant from
  ``provider_configs`` (``kind="llm"``), matching the ``TransportFactory``
  pattern.
"""

from packages.infrastructure.llm.factory import LLMClassifierFactory
from packages.infrastructure.llm.openrouter import (
    OpenRouterIntentClassifier,
    build_openrouter_classifier,
)

__all__ = [
    "LLMClassifierFactory",
    "OpenRouterIntentClassifier",
    "build_openrouter_classifier",
]

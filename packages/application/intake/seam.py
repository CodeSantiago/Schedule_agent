"""LLM intent-classification seam.

The seam is a `Protocol` so any object that satisfies `classify` works —
the deterministic `classify_intent` function in `packages.application.intake`
remains the default fallback, and the LLM-backed classifier
(`OpenRouterIntentClassifier`) receives full context (services, barbers,
business hours, session data, conversation history) to produce richer
responses with extracted data.
"""

from __future__ import annotations

from typing import Any, Optional, Protocol

from packages.application.intake import Intent


class IntentClassifier(Protocol):
    """Anything that turns a free-form text + current state into an Intent.

    Implementations must be safe to call from the webhook handler (no
    global state, no shared cache, no IO that can block the request
    thread for more than a few seconds). They may consult an LLM;
    they MUST degrade gracefully (return an `unknown` Intent) when the
    upstream is unavailable.

    The ``context`` dict carries tenant data the LLM needs to build a
    accurate response:
      - ``tenant_name`` — ``Tenant.name``
      - ``location`` — ``Tenant.location`` (optional)
      - ``services`` — list of formatted service strings
      - ``barbers`` — list of active barber strings
      - ``business_hours`` — formatted hours text
      - ``session_data`` — ``conversation_session.context`` (extracted data)
      - ``history`` — formatted conversation history
      - ``datetime_now`` — current datetime string
    """

    def classify(
        self,
        text: str,
        *,
        current_state: str,
        context: Optional[dict[str, Any]] = None,
    ) -> Intent: ...


class DeterministicIntentClassifier:
    """The default `IntentClassifier`: the pure-Python `classify_intent`.

    Ignores the ``context`` parameter — the deterministic classifier
    only needs the message text and current state.
    """

    def classify(
        self,
        text: str,
        *,
        current_state: str,
        context: Optional[dict[str, Any]] = None,
    ) -> Intent:
        # Local import to avoid a circular dependency at module load.
        from packages.application.intake import classify_intent

        return classify_intent(text, current_state=current_state)

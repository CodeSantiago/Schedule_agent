"""OpenRouter-backed intent classifier.

Implements the ``IntentClassifier`` Protocol from
``packages.application.intake.seam`` by calling the OpenRouter
Chat Completions API — a drop-in for the deterministic
``classify_intent`` function.

Environment variables (read at import time, can be overridden via
constructor arguments):

- ``OPENROUTER_API_KEY``  — required for real HTTP calls.
- ``OPENROUTER_BASE_URL`` — defaults to ``https://openrouter.ai/api/v1``.
- ``OPENROUTER_MODEL``    — defaults to ``openrouter/free``.

Fail-closed contract
--------------------
- When no API key is available at construction, ``classify`` immediately
  raises ``ConfigurationError``.  Callers that want a graceful fallback
  should catch ``ConfigurationError`` and use the deterministic classifier.
- ``build_openrouter_classifier()`` is the recommended factory: it returns
  a ``DeterministicIntentClassifier`` when the key is empty, so the
  webhook never crashes just because the env var is not set.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional
from uuid import UUID

from packages.application.intake import GREETING, Intent


# ── Known conversation states (must match `SESSION_STATE_VALUES`) ────────────

_VALID_STATES = frozenset({
    "start",
    "awaiting_menu",
    "awaiting_service",
    "awaiting_day",
    "awaiting_barber",
    "awaiting_time",
    "awaiting_name",
    "booking_confirmation",
    "booking_confirmed",
    "awaiting_cancellation",
    "booking_cancelled",
    "awaiting_reschedule",
    "selecting_cancel_appointment",
    "selecting_reschedule_appointment",
    "selecting_new_time",
    "booking_rescheduled",
    "idle",
    "closed",
})

# The system prompt is now built dynamically by `prompts.build_system_prompt()`.
# The old _SYSTEM_PROMPT constant is replaced by the dynamic builder so the LLM
# receives real tenant data (services, barbers, hours, etc.) on every request.


class ConfigurationError(RuntimeError):
    """Raised when the LLM classifier cannot be used because configuration
    is missing or incomplete."""


class OpenRouterIntentClassifier:
    """Intent classifier backed by the OpenRouter API.

    Builds a dynamic system prompt per request with the tenant's actual
    services, barbers, business hours, session data and conversation
    history so the LLM can classify intents and extract booking
    information naturally.

    Usage::

        classifier = OpenRouterIntentClassifier(api_key="sk-...")
        intent =         classifier.classify(
            "quiero un corte con lean",
            current_state="awaiting_menu",
            context={
                "tenant_name": "Barbería X",
                "services": [{"name": "Corte", "price_cents": 2500, "duration_min": 30}],
                "barbers": [{"name": "Lean", "available_days": "lun-sáb"}],
                "business_hours": "Lun-vie 9-20, Sáb 9-14",
            },
        )
        print(intent.kind)  # "book"

    Real HTTP calls are made only when ``api_key`` is provided.
    If the key is empty or ``None``, ``classify`` raises
    ``ConfigurationError``.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        http_client: Any = None,
    ) -> None:
        self._api_key = api_key or os.environ.get("OPENROUTER_API_KEY") or ""
        self._base_url = (
            base_url
            or os.environ.get("OPENROUTER_BASE_URL")
            or "https://openrouter.ai/api/v1"
        ).rstrip("/")
        self._model = model or os.environ.get("OPENROUTER_MODEL") or "openrouter/free"
        self._http_client = http_client

        if not self._api_key:
            raise ConfigurationError(
                "OPENROUTER_API_KEY is not set. Set it in the environment "
                "or pass api_key to OpenRouterIntentClassifier()."
            )

    def classify(
        self,
        text: str,
        *,
        current_state: str = "start",
        context: Optional[dict[str, Any]] = None,
    ) -> Intent:
        """Classify ``text`` via OpenRouter and return an ``Intent``.

        ``context`` may carry tenant data for the dynamic prompt:
          - tenant_name, location, services, barbers, business_hours
          - session_data, history, datetime_now

        Raises:
            ConfigurationError: if the API key was not provided at init.
            RuntimeError: if the API call fails or the response cannot
                be parsed.
        """
        if not self._api_key:
            raise ConfigurationError(
                "OpenRouter API key not configured — cannot classify."
            )

        ctx = context or {}

        from packages.infrastructure.llm.prompts import (
            build_conversation_prompt,
            build_system_prompt,
        )

        system_prompt = build_system_prompt(
            tenant_name=ctx.get("tenant_name", "la barbería"),
            location=ctx.get("location"),
            services=ctx.get("services"),
            barbers=ctx.get("barbers"),
            business_hours=ctx.get("business_hours"),
        )

        user_prompt = build_conversation_prompt(
            current_state=current_state,
            session_data=ctx.get("session_data"),
            history=ctx.get("history"),
            datetime_now=ctx.get("datetime_now"),
            user_message=text,
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": 600,
        }

        url = f"{self._base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/sayer/barber-agent",
            "X-Title": "Barber Agent",
        }

        raw = self._call_api(url, headers, payload)
        return self._parse_response(raw, current_state, context=ctx)

    # ── Internal helpers ─────────────────────────────────────────────────

    # Maximum retries for transient API failures.
    _MAX_RETRIES: int = 2
    # Base delay for exponential backoff (seconds).
    _RETRY_BASE_DELAY: float = 1.0

    def _call_api(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> str:
        """Make the real HTTP POST to OpenRouter with retries.

        Retries on transient failures (5xx, network errors) with
        exponential backoff. Permanent failures (4xx, parse errors)
        raise immediately.

        Uses the injected ``http_client`` when available, otherwise
        creates a one-shot ``httpx`` client.
        """
        import time as _time

        last_error: str | None = None

        for attempt in range(self._MAX_RETRIES + 1):
            try:
                if self._http_client is not None:
                    resp = self._http_client.post(
                        url, headers=headers, json=payload
                    )
                else:
                    import httpx as _httpx

                    with _httpx.Client(timeout=30.0) as client:
                        resp = client.post(
                            url, headers=headers, json=payload
                        )

                if resp.status_code == 200:
                    data = resp.json()
                    choices = data.get("choices", [])
                    if not choices:
                        raise RuntimeError(
                            f"OpenRouter response has no choices: "
                            f"{json.dumps(data)[:300]}"
                        )
                    content = (
                        choices[0].get("message", {}).get("content", "")
                    )
                    return content.strip()

                # 4xx errors are permanent — do not retry.
                if 400 <= resp.status_code < 500:
                    raise RuntimeError(
                        f"OpenRouter API returned {resp.status_code}: "
                        f"{resp.text[:300]}"
                    )

                # 5xx errors are transient — retry.
                last_error = (
                    f"OpenRouter API returned {resp.status_code}: "
                    f"{resp.text[:200]}"
                )

            except RuntimeError:
                raise  # Re-raise permanent failures.
            except Exception as exc:
                last_error = f"OpenRouter transport error: {exc}"

            if attempt < self._MAX_RETRIES:
                delay = self._RETRY_BASE_DELAY * (2 ** attempt)
                _time.sleep(delay)

        raise RuntimeError(
            f"OpenRouter API call failed after {self._MAX_RETRIES + 1} "
            f"attempts. Last error: {last_error}"
        )

    def _parse_response(
        self,
        raw: str,
        current_state: str,
        *,
        context: Optional[dict[str, Any]] = None,
    ) -> Intent:
        """Parse the LLM's JSON response into an ``Intent`` dataclass.

        Handles the ``extracted`` field and validates all values.
        Falls back to ``kind="unknown"`` when the JSON is malformed or
        missing required fields.
        """
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            cleaned = "\n".join(
                line for line in lines if not line.startswith("```")
            ).strip()

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            return self._fallback_intent(current_state)

        # ── kind ────────────────────────────────────────────────────────
        kind = str(data.get("kind", "unknown")).lower().strip()
        valid_kinds = ("book", "cancel", "reschedule", "info", "unknown")
        if kind not in valid_kinds:
            kind = "unknown"

        # ── next_state ──────────────────────────────────────────────────
        next_state = str(data.get("next_state", current_state)).lower().strip()
        if next_state not in _VALID_STATES:
            next_state = current_state

        # ── reply ───────────────────────────────────────────────────────
        reply = str(data.get("reply", "")).strip()
        if not reply:
            from packages.application.intake import GREETING as _GREETING

            reply = _GREETING if current_state == "start" else (
                "No entendí. Por favor elegí 1 para agendar, "
                "2 para cancelar o 3 para reagendar."
            )

        # ── extracted (aditive merge hint) ──────────────────────────────
        extracted_raw = data.get("extracted")
        extracted: dict[str, Any] = {}
        if isinstance(extracted_raw, dict):
            extracted = {
                str(k): v
                for k, v in extracted_raw.items()
                if v is not None and v != ""
            }

        # ── Output validation (security hardening) ──────────────────
        from packages.infrastructure.security import validate_classifier_output

        validation_issues = validate_classifier_output(
            kind,
            next_state,
            reply,
            extracted,
            valid_states=_VALID_STATES,
        )
        if validation_issues:
            # Validation failed — fall back to unknown/current state
            # rather than returning potentially unsafe output.
            return self._fallback_intent(current_state)

        return Intent(
            kind=kind, next_state=next_state, reply=reply, extracted=extracted,
        )

    @staticmethod
    def _fallback_intent(current_state: str) -> Intent:
        """Return a safe fallback when the LLM response is unparseable."""
        return Intent(
            kind="unknown",
            next_state=current_state,
            reply=(
                "No entendí. Por favor elegí 1 para agendar, "
                "2 para cancelar o 3 para reagendar."
            ),
            extracted={},
        )


# ── Convenience factory ──────────────────────────────────────────────────────


def build_openrouter_classifier(
    *,
    api_key: str | None = None,
    model: str | None = None,
    http_client: Any = None,
) -> Any:
    """Build the best available intent classifier.

    When ``OPENROUTER_API_KEY`` is set (in the environment or via the
    ``api_key`` argument), returns an ``OpenRouterIntentClassifier``
    that makes real HTTP calls.

    Otherwise returns a ``DeterministicIntentClassifier`` (the default
    pure-Python classifier) — the webhook keeps working without any
    LLM dependency.

    The return type satisfies the ``IntentClassifier`` Protocol from
    ``packages.application.intake.seam``.
    """
    effective_key = api_key or os.environ.get("OPENROUTER_API_KEY") or ""
    if effective_key:
        return OpenRouterIntentClassifier(
            api_key=effective_key,
            model=model,
            http_client=http_client,
        )
    from packages.application.intake.seam import DeterministicIntentClassifier

    return DeterministicIntentClassifier()

"""Tests for the real integration wiring — OpenRouter classifier,
KapsoTransport fail-closed behaviour, webhook signature verification,
and the classifier factory.

These tests pin the **contract** that the production paths will satisfy.
They use mocks / in-process stubs to avoid real network calls.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from packages.application.intake import GREETING, build_intent_classifier, classify_intent
from packages.application.messaging import KapsoTransport, SendResult
from packages.application.providers.kapso_config import verify_webhook_signature


# ── KapsoTransport: fail-closed contract ─────────────────────────────────---


class TestKapsoTransportFailClosed:
    def test_no_api_key_returns_delivered_false(self) -> None:
        """Constructed without api_key → send() fails clearly."""
        t = KapsoTransport({})
        out = t.send(to_phone="+5491100000000", body="hola")
        assert out.delivered is False
        assert out.provider_message_id is None
        assert out.error is not None
        assert "not configured" in (out.error or "").lower()

    def test_empty_api_key_returns_delivered_false(self) -> None:
        """api_key='' is treated the same as missing."""
        t = KapsoTransport({"api_key": ""})
        out = t.send(to_phone="+5491100000000", body="hola")
        assert out.delivered is False
        assert out.error is not None
        assert "not configured" in (out.error or "").lower()

    def test_whitespace_api_key_fails(self) -> None:
        """api_key='   ' is treated as empty."""
        t = KapsoTransport({"api_key": "   "})
        out = t.send(to_phone="+5491100000000", body="hola")
        assert out.delivered is False

    def test_missing_all_config_keys(self) -> None:
        """Empty config dict → fail closed."""
        t = KapsoTransport({})
        out = t.send(to_phone="+5491100000000", body="hi")
        assert out.delivered is False
        # The error should mention the solution.
        assert "set api_key" in (out.error or "").lower()


# ── Webhook signature verification ──────────────────────────────────────────


class TestVerifyWebhookSignature:
    def test_disabled_when_secret_empty(self) -> None:
        """No webhook_secret → verification passes."""
        assert verify_webhook_signature(
            raw_body=b'{"hello":"world"}',
            signature_header="some-signature",
            webhook_secret=None,
        ) is True

    def test_disabled_when_secret_blank(self) -> None:
        assert verify_webhook_signature(
            raw_body=b"{}",
            signature_header="x",
            webhook_secret="",
        ) is True

    def test_missing_header_when_secret_set(self) -> None:
        """Secret configured but no signature header → fail."""
        assert verify_webhook_signature(
            raw_body=b"{}",
            signature_header=None,
            webhook_secret="whsec_abc",
        ) is False

    def test_valid_signature_passes(self) -> None:
        """HMAC-SHA256 of the body matches the supplied header."""
        import hashlib
        import hmac

        body = b'{"msg":"hello"}'
        secret = "whsec_test123"
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        assert verify_webhook_signature(
            raw_body=body,
            signature_header=expected,
            webhook_secret=secret,
        ) is True

    def test_invalid_signature_fails(self) -> None:
        """Wrong signature does not match."""
        assert verify_webhook_signature(
            raw_body=b'{"msg":"hello"}',
            signature_header="0000000000000000000000000000000000000000000000000000000000000000",
            webhook_secret="whsec_test123",
        ) is False

    def test_different_body_fails(self) -> None:
        """Same secret, different body → different signature → fail."""
        import hashlib
        import hmac

        body = b'{"msg":"original"}'
        secret = "whsec_test"
        signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        assert verify_webhook_signature(
            raw_body=b'{"msg":"tampered"}',
            signature_header=signature,
            webhook_secret=secret,
        ) is False


# ── Build intent classifier factory ─────────────────────────────────────────


class TestBuildIntentClassifier:
    def test_returns_deterministic_when_no_env_key(self) -> None:
        """No OPENROUTER_API_KEY → DeterministicIntentClassifier."""
        classifier = build_intent_classifier()
        from packages.application.intake import DeterministicIntentClassifier

        assert isinstance(classifier, DeterministicIntentClassifier)
        # It should behave identically to classify_intent.
        intent = classifier.classify("1", current_state="start")
        expected = classify_intent("1", current_state="start")
        assert intent.kind == expected.kind == "book"

    def test_returns_openrouter_when_key_provided(self) -> None:
        """api_key argument → OpenRouterIntentClassifier."""
        from packages.infrastructure.llm import OpenRouterIntentClassifier

        # We need a key to construct it — but we can use a mock http_client.
        mock_client = _MockHttpClient(json_response={"choices": [{"message": {"content": json.dumps({
            "kind": "book",
            "next_state": "awaiting_service",
            "reply": "Decime el servicio: 1 Corte, 2 Barba, 3 Corte y Barba.",
        })}}]})
        classifier = OpenRouterIntentClassifier(
            api_key="sk-mock",
            model="openrouter/free",
            http_client=mock_client,
        )
        intent = classifier.classify("1", current_state="start")
        assert intent.kind == "book"
        assert intent.next_state == "awaiting_service"
        assert "servicio" in intent.reply.lower()

    def test_openrouter_without_key_raises(self) -> None:
        """OpenRouterIntentClassifier without key raises ConfigurationError."""
        from packages.infrastructure.llm.openrouter import (
            ConfigurationError,
            OpenRouterIntentClassifier,
        )

        with pytest.raises(ConfigurationError):
            OpenRouterIntentClassifier(api_key="")


# ── OpenRouter classifier: parsing ──────────────────────────────────────────


class TestOpenRouterParsing:
    def test_parses_valid_json(self) -> None:
        from packages.infrastructure.llm.openrouter import (
            OpenRouterIntentClassifier,
        )

        c = OpenRouterIntentClassifier(api_key="sk-test", http_client=_MockHttpClient(json_response={
            "choices": [{"message": {"content": '{"kind":"cancel","next_state":"awaiting_cancellation","reply":"Listo. Decime el nombre."}'}}]
        }))
        intent = c.classify("2", current_state="start")
        assert intent.kind == "cancel"
        assert intent.next_state == "awaiting_cancellation"

    def test_parses_json_with_markdown_fence(self) -> None:
        from packages.infrastructure.llm.openrouter import (
            OpenRouterIntentClassifier,
        )

        c = OpenRouterIntentClassifier(api_key="sk-test", http_client=_MockHttpClient(json_response={
            "choices": [{"message": {"content": '```json\n{"kind":"book","next_state":"awaiting_service","reply":"Elegí servicio."}\n```'}}]
        }))
        intent = c.classify("1", current_state="start")
        assert intent.kind == "book"
        assert intent.next_state == "awaiting_service"

    def test_falls_back_on_bad_json(self) -> None:
        from packages.infrastructure.llm.openrouter import (
            OpenRouterIntentClassifier,
        )

        c = OpenRouterIntentClassifier(api_key="sk-test", http_client=_MockHttpClient(json_response={
            "choices": [{"message": {"content": "this is not json"}}]
        }))
        intent = c.classify("x", current_state="start")
        assert intent.kind == "unknown"
        # Falls back to current state, not advancing.
        assert intent.next_state == "start"

    def test_falls_back_on_empty_choices(self) -> None:
        from packages.infrastructure.llm.openrouter import (
            OpenRouterIntentClassifier,
        )

        c = OpenRouterIntentClassifier(api_key="sk-test", http_client=_MockHttpClient(json_response={
            "choices": []
        }))
        with pytest.raises(RuntimeError, match="no choices"):
            c.classify("hi", current_state="start")

    def test_api_error_raises(self) -> None:
        from packages.infrastructure.llm.openrouter import (
            OpenRouterIntentClassifier,
        )

        c = OpenRouterIntentClassifier(api_key="sk-test", http_client=_MockHttpClient(
            json_response={},
            status_code=500,
            text="Internal Server Error",
        ))
        with pytest.raises(RuntimeError, match="500"):
            c.classify("hi", current_state="start")


# ── Helper ──────────────────────────────────────────────────────────────────


class _MockHttpClient:
    """Minimal mock ``httpx.Client`` for LLM classifier tests."""

    class _MockResponse:
        def __init__(self, json_data: dict, status_code: int, text: str) -> None:
            self.status_code = status_code
            self.is_success = 200 <= status_code < 300
            self._json_data = json_data
            self._text = text

        def json(self) -> dict:
            return self._json_data

        @property
        def text(self) -> str:
            return self._text

    def __init__(
        self,
        json_response: dict | None = None,
        status_code: int = 200,
        text: str = "",
    ) -> None:
        self._json = json_response or {"choices": []}
        self._status = status_code
        self._text = text or json.dumps(self._json)

    def post(
        self, url: str, *, headers: dict | None = None, json: dict | None = None, timeout: float | None = None  # noqa: ARG002
    ) -> _MockResponse:
        return self._MockResponse(self._json, self._status, self._text)

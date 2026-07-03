"""Tests for the deterministic intake classifier.

``classify_intent`` is pure data, so the test suite is just a small
table of inputs and expected outputs. The webhook E2E tests in
``tests/api/test_webhook_route.py`` exercise the same logic through
the full pipeline.
"""

from __future__ import annotations

import pytest

from packages.application.intake import GREETING, Intent, classify_intent


class TestClassifyIntent:
    def test_book_at_top_of_funnel(self) -> None:
        intent = classify_intent("1", current_state="start")
        assert intent.kind == "book"
        assert intent.next_state == "awaiting_service"
        assert "servicio" in intent.reply

    def test_cancel_at_top_of_funnel(self) -> None:
        intent = classify_intent("2", current_state="start")
        assert intent.kind == "cancel"
        assert intent.next_state == "awaiting_cancellation"
        assert "cancel" in intent.reply.lower()

    def test_reschedule_at_top_of_funnel(self) -> None:
        intent = classify_intent("3", current_state="start")
        assert intent.kind == "reschedule"
        assert intent.next_state == "awaiting_reschedule"

    def test_idle_state_behaves_like_start(self) -> None:
        # A returning customer in ``idle`` should see the same menu.
        for state in ("start", "awaiting_menu", "idle"):
            intent = classify_intent("1", current_state=state)
            assert intent.kind == "book", state

    def test_unknown_input_returns_fallback_intent(self) -> None:
        intent = classify_intent("hola que tal", current_state="start")
        assert intent.kind == "unknown"
        assert intent.next_state == "start"
        # The fallback should mention the menu options so the customer
        # can self-correct.
        assert "1" in intent.reply and "2" in intent.reply and "3" in intent.reply

    def test_empty_string_returns_fallback(self) -> None:
        intent = classify_intent("", current_state="start")
        assert intent.kind == "unknown"

    def test_intent_is_a_frozen_dataclass(self) -> None:
        intent = classify_intent("1", current_state="start")
        assert isinstance(intent, Intent)
        with pytest.raises(Exception):
            intent.kind = "tampered"  # type: ignore[misc]

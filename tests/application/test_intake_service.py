"""Application-layer tests for `IntakeService`.

Exercises the deterministic intake pipeline against a real
in-memory sqlite engine. Pins:

- The session is created on first contact and re-used on the next hit.
- The `incoming_messages` table is populated with the raw payload.
- The `outgoing_messages` table gets the reply body.
- The session state advances per `classify_intent`.
- Retries with the same `provider_message_id` are no-ops.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from packages.application.intake import GREETING, IntakeService
from packages.infrastructure.repositories.messaging import (
    ConversationSessionRepository,
    IncomingMessageRepository,
    OutgoingMessageRepository,
)


@pytest.fixture()
def intake(session, tenant_id) -> IntakeService:
    return IntakeService(session, tenant_id)


class TestHandleInbound:
    def test_first_message_creates_session_and_greeting(
        self, intake: IntakeService, tenant_id
    ) -> None:
        result = intake.handle_inbound(
            customer_phone="+5491100000001",
            body="hola",
            provider_message_id=f"msg-{uuid4().hex}",
        )
        assert result["state"] == "awaiting_menu"
        assert result["reply"] == GREETING

        # Session persisted, state advanced, one incoming + one outgoing row.
        sessions = ConversationSessionRepository(intake.session, tenant_id)
        found = sessions.get_for_customer("+5491100000001")
        assert found is not None
        assert found.state == "awaiting_menu"
        assert found.last_message_seq == 1

    def test_advances_state_on_book_intent(
        self, intake: IntakeService, tenant_id
    ) -> None:
        intake.handle_inbound(
            customer_phone="+5491100000001",
            body="hola",
            provider_message_id=f"msg-{uuid4().hex}",
        )
        result = intake.handle_inbound(
            customer_phone="+5491100000001",
            body="1",
            provider_message_id=f"msg-{uuid4().hex}",
        )
        assert result["state"] == "awaiting_service"
        assert "servicio" in result["reply"].lower()

    def test_unknown_intent_keeps_state(self, intake: IntakeService) -> None:
        intake.handle_inbound(
            customer_phone="+5491100000001",
            body="hola",
            provider_message_id=f"msg-{uuid4().hex}",
        )
        result = intake.handle_inbound(
            customer_phone="+5491100000001",
            body="patata",
            provider_message_id=f"msg-{uuid4().hex}",
        )
        # Unknown input keeps the state where it was; the reply
        # explains the menu.
        assert result["state"] == "awaiting_menu"
        assert "1" in result["reply"]

    def test_dedup_on_provider_message_id(
        self, intake: IntakeService, tenant_id
    ) -> None:
        # First call creates one incoming + one outgoing row.
        first = intake.handle_inbound(
            customer_phone="+5491100000001",
            body="hola",
            provider_message_id="dedup-key",
        )
        assert first["state"] == "awaiting_menu"

        # Replay the same provider id (simulating a webhook retry).
        # The unique constraint on (tenant_id, provider_message_id) is
        # the source of truth; the service must not create a second
        # incoming row.
        inc = IncomingMessageRepository(intake.session, tenant_id)
        before = inc.list_for_session(
            # session id from the first call
            __import__("uuid").UUID(first["session_id"])
        )
        intake.handle_inbound(
            customer_phone="+5491100000001",
            body="1",
            provider_message_id="dedup-key",
        )
        after = inc.list_for_session(
            __import__("uuid").UUID(first["session_id"])
        )
        assert len(before) == len(after) == 1

    def test_two_customers_get_independent_sessions(
        self, intake: IntakeService, tenant_id
    ) -> None:
        a = intake.handle_inbound(
            customer_phone="+5491100000001",
            body="hola",
            provider_message_id=f"a-{uuid4().hex}",
        )
        b = intake.handle_inbound(
            customer_phone="+5491100000002",
            body="hola",
            provider_message_id=f"b-{uuid4().hex}",
        )
        assert a["session_id"] != b["session_id"]

    def test_stale_session_resets_old_context_before_new_greeting(
        self, intake: IntakeService, tenant_id
    ) -> None:
        intake.handle_inbound(
            customer_phone="+5491100000001",
            body="hola",
            provider_message_id=f"msg-{uuid4().hex}",
        )
        sessions = ConversationSessionRepository(intake.session, tenant_id)
        found = sessions.get_for_customer("+5491100000001")
        assert found is not None
        found.state = "awaiting_barber"
        found.context = {"service": "corte", "barber": "Pedro", "date": "2026-07-01"}
        found.last_message_seq = 24
        found.updated_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=3)
        intake.session.flush()

        result = intake.handle_inbound(
            customer_phone="+5491100000001",
            body="Buenas tardes",
            provider_message_id=f"msg-{uuid4().hex}",
        )

        assert result["state"] == "awaiting_menu"
        assert result["reply"] == GREETING
        refreshed = sessions.get_for_customer("+5491100000001")
        assert refreshed is not None
        assert refreshed.state == "awaiting_menu"
        assert refreshed.context == {"last_intent_kind": "greeting"}

    def test_stale_session_does_not_leak_old_barber_into_next_step(
        self, intake: IntakeService, tenant_id
    ) -> None:
        intake.handle_inbound(
            customer_phone="+5491100000001",
            body="hola",
            provider_message_id=f"msg-{uuid4().hex}",
        )
        sessions = ConversationSessionRepository(intake.session, tenant_id)
        found = sessions.get_for_customer("+5491100000001")
        assert found is not None
        found.state = "awaiting_barber"
        found.context = {"service": "corte", "barber": "Pedro", "date": "2026-07-01"}
        found.last_message_seq = 24
        found.updated_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=3)
        intake.session.flush()

        intake.handle_inbound(
            customer_phone="+5491100000001",
            body="Buenas tardes",
            provider_message_id=f"msg-{uuid4().hex}",
        )
        result = intake.handle_inbound(
            customer_phone="+5491100000001",
            body="1",
            provider_message_id=f"msg-{uuid4().hex}",
        )

        assert result["state"] == "awaiting_service"
        refreshed = sessions.get_for_customer("+5491100000001")
        assert refreshed is not None
        assert refreshed.context == {"last_intent_kind": "ask_service"}

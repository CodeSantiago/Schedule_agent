"""Tests for bot-to-booking integration via IntakeService.

When the classifier produces ``booking_confirmed`` (the customer confirmed
the booking), ``IntakeService`` attempts to persist the appointment
through ``BookingService.book_slot()``. These tests verify that:

- A successful booking creates an appointment row and advances state.
- A failed booking (slot taken, past time, etc.) returns a friendly
  error and keeps the session in its current state.
- Missing/incomplete data is reported to the customer.
- The service/barber name resolution works case-insensitively.
"""

from __future__ import annotations

from datetime import date, datetime, time
from uuid import uuid4

import pytest

from packages.application.intake import GREETING, Intent, IntakeService
from packages.application.intake.seam import IntentClassifier
from packages.domain.scheduling.errors import (
    SlotTakenError,
)
from packages.infrastructure.db.models.appointments import Appointment
from packages.infrastructure.db.models.scheduling import (
    Barber,
    BarberSchedule,
    Service,
)
from packages.infrastructure.repositories import (
    AppointmentRepository,
)
from packages.infrastructure.repositories.messaging import (
    ConversationSessionRepository,
)


# ── Helpers ────────────────────────────────────────────────────────────────

WEDNESDAY = date(2026, 6, 24)


def _make_barber(session, tenant_id, name="O"):
    b = Barber(id=uuid4(), tenant_id=tenant_id, name=name, is_active=True)
    session.add(b)
    session.flush()
    return b


def _make_schedule(session, barber_id, weekday="wed", start=time(10, 0), end=time(20, 0)):
    sch = BarberSchedule(
        id=uuid4(),
        barber_id=barber_id,
        weekday=weekday,
        start_time=start,
        end_time=end,
    )
    session.add(sch)
    session.flush()
    return sch


def _make_service(session, tenant_id, name="Corte", duration_minutes=30, code="C"):
    s = Service(
        id=uuid4(),
        tenant_id=tenant_id,
        name=name,
        code=code,
        duration_minutes=duration_minutes,
        price_cents=2500,
        is_active=True,
    )
    session.add(s)
    session.flush()
    return s


def _build_context(overrides: dict | None = None) -> dict:
    """Build a minimal session context with booking data.

    Uses English keys (the normalized format). The backward-compat
    shim ``_normalize_session_context`` handles old sessions that
    still contain Spanish keys like ``servicio`` / ``barbero``.
    """
    ctx = {
        "service": "Corte",
        "barber": "Lean",
        "date": "2026-06-24",
        "time": "11:00",
        "name": "Alice",
        "last_intent_kind": "ask_service",
    }
    if overrides:
        ctx.update(overrides)
    return ctx


class MockConfirmClassifier:
    """A classifier that always moves to ``booking_confirmed``."""

    def __init__(self, reply: str = "¡Listo! Tu turno está confirmado.") -> None:
        self._reply = reply

    def classify(
        self,
        text: str,
        *,
        current_state: str = "start",
        context: dict | None = None,
    ) -> Intent:
        return Intent(
            kind="book",
            next_state="booking_confirmed",
            reply=self._reply,
            extracted={},
        )


# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture()
def intake(session, tenant_id) -> IntakeService:
    return IntakeService(session, tenant_id)


@pytest.fixture()
def seeded_session(session, tenant_id, make_tenant):
    """Set up a tenant with one barber (Lean), one service (Corte), and a
    Wednesday schedule, plus a conversation session in ``booking_confirmation``
    state with booking data already in context."""
    tenant = make_tenant("A")
    tid = tenant.id
    barber = _make_barber(session, tid, name="Lean")
    _make_schedule(session, barber.id)
    service = _make_service(session, tid, name="Corte", code="C")
    session.commit()

    # Pre-create a conversation session with booking context.
    sessions = ConversationSessionRepository(session, tid)
    conv, _ = sessions.find_or_create("+5491100000001")
    sessions.advance(
        conv,
        new_state="booking_confirmation",
        context_patch=_build_context(),
    )
    session.commit()

    return {
        "tenant_id": tid,
        "barber": barber,
        "service": service,
        "phone": "+5491100000001",
    }


# ── Tests ──────────────────────────────────────────────────────────────────


class TestSuccessfulBooking:
    """Booking succeeds → appointment row created, state advances."""

    def test_successful_booking_creates_appointment(
        self, session, seeded_session
    ) -> None:
        tid = seeded_session["tenant_id"]
        barber = seeded_session["barber"]

        intake = IntakeService(session, tid)
        result = intake.handle_inbound(
            customer_phone=seeded_session["phone"],
            body="si",
            provider_message_id="confirm-001",
            classifier=MockConfirmClassifier(),
        )
        session.commit()

        assert result["state"] == "booking_confirmed"
        assert "confirmado" in result["reply"].lower() or "listo" in result["reply"].lower()

        # Verify appointment was created.
        appts = AppointmentRepository(session, tid).get_for_barber_on(
            barber.id, WEDNESDAY
        )
        assert len(appts) == 1
        assert appts[0].customer_name == "Alice"
        assert appts[0].customer_phone == seeded_session["phone"]
        assert appts[0].notes == "Agendado vía WhatsApp"

    def test_case_insensitive_name_matching(
        self, session, seeded_session
    ) -> None:
        """Booking data with different case should still match."""
        tid = seeded_session["tenant_id"]
        barber = seeded_session["barber"]

        # Overwrite context with mixed-case names.
        sessions = ConversationSessionRepository(session, tid)
        conv = sessions.get_for_customer(seeded_session["phone"])
        sessions.advance(
            conv,
            new_state="booking_confirmation",
            context_patch=_build_context({
                "barber": "lean",  # lower case
                "service": "corte",  # lower case
            }),
        )
        session.commit()

        intake = IntakeService(session, tid)
        result = intake.handle_inbound(
            customer_phone=seeded_session["phone"],
            body="si",
            provider_message_id="confirm-002",
            classifier=MockConfirmClassifier(),
        )
        session.commit()

        assert result["state"] == "booking_confirmed"


class TestFailedBooking:
    """Booking fails → state unchanged, customer gets friendly error."""

    def test_slot_taken_returns_friendly_error(
        self, session, seeded_session
    ) -> None:
        """Pre-book the slot, then try to book again via the bot."""
        tid = seeded_session["tenant_id"]
        barber = seeded_session["barber"]
        service = seeded_session["service"]

        # Pre-book the slot via BookingService.
        from packages.application.scheduling.booking_service import (
            BookSlotCommand,
            BookingService,
        )

        svc = BookingService(session, tid)
        svc.book_slot(
            BookSlotCommand(
                tenant_id=tid,
                barber_id=barber.id,
                service_id=service.id,
                start_at=datetime(WEDNESDAY.year, WEDNESDAY.month, WEDNESDAY.day, 11, 0),
                customer_name="Blocker",
                customer_phone="+5491100000099",
            )
        )
        session.commit()

        intake = IntakeService(session, tid)
        result = intake.handle_inbound(
            customer_phone=seeded_session["phone"],
            body="si",
            provider_message_id="confirm-003",
            classifier=MockConfirmClassifier(),
        )
        session.commit()

        # Should NOT advance to booking_confirmed.
        assert result["state"] == "booking_confirmation"
        assert "reservado" in result["reply"].lower()

        # No second appointment row.
        appts = AppointmentRepository(session, tid).get_for_barber_on(
            barber.id, WEDNESDAY
        )
        assert len(appts) == 1  # still just the blocker

    def test_past_time_error(
        self, session, seeded_session
    ) -> None:
        """Booking a slot in the past should be rejected."""
        tid = seeded_session["tenant_id"]

        # Override context to a past time (today, one hour ago).
        from datetime import timedelta
        past = datetime.now() - timedelta(hours=1)
        sessions = ConversationSessionRepository(session, tid)
        conv = sessions.get_for_customer(seeded_session["phone"])
        sessions.advance(
            conv,
            new_state="booking_confirmation",
            context_patch=_build_context({
                "date": past.strftime("%Y-%m-%d"),
                "time": past.strftime("%H:%M"),
            }),
        )
        session.commit()

        intake = IntakeService(session, tid)
        result = intake.handle_inbound(
            customer_phone=seeded_session["phone"],
            body="si",
            provider_message_id="confirm-004",
            classifier=MockConfirmClassifier(),
        )
        session.commit()

        assert result["state"] == "booking_confirmation"
        assert "pasó" in result["reply"].lower() or "paso" in result["reply"].lower()

    def test_missing_data_returns_error(
        self, session, seeded_session
    ) -> None:
        """When booking data is missing, the customer gets a clear message."""
        tid = seeded_session["tenant_id"]

        # Clear the booking context by overwriting each field.
        sessions = ConversationSessionRepository(session, tid)
        conv = sessions.get_for_customer(seeded_session["phone"])
        sessions.advance(
            conv,
            new_state="booking_confirmation",
            context_patch={
                "service": "",
                "barber": "",
                "date": "",
                "time": "",
                "name": "",
            },
        )
        session.commit()

        intake = IntakeService(session, tid)
        result = intake.handle_inbound(
            customer_phone=seeded_session["phone"],
            body="si",
            provider_message_id="confirm-005",
            classifier=MockConfirmClassifier(),
        )
        session.commit()

        assert result["state"] == "booking_confirmation"
        assert "falta" in result["reply"].lower() or "Faltan" in result["reply"]

    def test_unknown_service_name(
        self, session, seeded_session
    ) -> None:
        """A service name that doesn't match any active service."""
        tid = seeded_session["tenant_id"]

        sessions = ConversationSessionRepository(session, tid)
        conv = sessions.get_for_customer(seeded_session["phone"])
        sessions.advance(
            conv,
            new_state="booking_confirmation",
            context_patch=_build_context({"service": "Manicura"}),
        )
        session.commit()

        intake = IntakeService(session, tid)
        result = intake.handle_inbound(
            customer_phone=seeded_session["phone"],
            body="si",
            provider_message_id="confirm-006",
            classifier=MockConfirmClassifier(),
        )
        session.commit()

        assert result["state"] == "booking_confirmation"
        assert "no encontré" in result["reply"].lower() or "Manicura" in result["reply"]

    def test_unknown_barber_name(
        self, session, seeded_session
    ) -> None:
        """A barber name that doesn't match any active barber."""
        tid = seeded_session["tenant_id"]

        sessions = ConversationSessionRepository(session, tid)
        conv = sessions.get_for_customer(seeded_session["phone"])
        sessions.advance(
            conv,
            new_state="booking_confirmation",
            context_patch=_build_context({"barber": "Inexistente"}),
        )
        session.commit()

        intake = IntakeService(session, tid)
        result = intake.handle_inbound(
            customer_phone=seeded_session["phone"],
            body="si",
            provider_message_id="confirm-007",
            classifier=MockConfirmClassifier(),
        )
        session.commit()

        assert result["state"] == "booking_confirmation"
        assert "no encontré" in result["reply"].lower() or "Inexistente" in result["reply"]

    def test_invalid_date_format(
        self, session, seeded_session
    ) -> None:
        """An unparseable date in the context should be handled."""
        tid = seeded_session["tenant_id"]

        sessions = ConversationSessionRepository(session, tid)
        conv = sessions.get_for_customer(seeded_session["phone"])
        sessions.advance(
            conv,
            new_state="booking_confirmation",
            context_patch=_build_context({"date": "not-a-date"}),
        )
        session.commit()

        intake = IntakeService(session, tid)
        result = intake.handle_inbound(
            customer_phone=seeded_session["phone"],
            body="si",
            provider_message_id="confirm-008",
            classifier=MockConfirmClassifier(),
        )
        session.commit()

        assert result["state"] == "booking_confirmation"
        assert "válida" in result["reply"].lower() or "válidas" in result["reply"].lower()


class TestBackwardCompatLegacySpanishKeys:
    """Old sessions persisted with Spanish context keys must still work.

    Before the codebase normalization, session context used Spanish keys
    like ``servicio``, ``barbero``, ``fecha``, etc. The compat shim
    ``_normalize_session_context`` maps these to English on read so
    existing sessions function without a DB migration.
    """

    def test_spanish_context_keys_still_work(
        self, session, seeded_session
    ) -> None:
        """Set up context with old Spanish keys; booking should succeed."""
        tid = seeded_session["tenant_id"]

        # Overwrite context with the LEGACY Spanish keys.
        sessions = ConversationSessionRepository(session, tid)
        conv = sessions.get_for_customer(seeded_session["phone"])
        sessions.advance(
            conv,
            new_state="booking_confirmation",
            context_patch={
                "servicio": "Corte",
                "barbero": "Lean",
                "fecha": "2026-06-24",
                "hora": "11:00",
                "nombre": "Alice",
            },
        )
        session.commit()

        intake = IntakeService(session, tid)
        result = intake.handle_inbound(
            customer_phone=seeded_session["phone"],
            body="si",
            provider_message_id="compat-001",
            classifier=MockConfirmClassifier(),
        )
        session.commit()

        assert result["state"] == "booking_confirmed", (
            f"Expected booking_confirmed but got {result['state']}: "
            f"{result['reply']}"
        )

    def test_mixed_old_and_new_keys(
        self, session, seeded_session
    ) -> None:
        """Context with a mix of old Spanish and new English keys works.

        When both old and new keys are present, the English value wins.
        This simulates a session that was partially migrated.
        """
        tid = seeded_session["tenant_id"]

        sessions = ConversationSessionRepository(session, tid)
        conv = sessions.get_for_customer(seeded_session["phone"])
        sessions.advance(
            conv,
            new_state="booking_confirmation",
            context_patch={
                "servicio": "Manicura",  # old key, stale value
                "service": "Corte",       # new key, correct value
                "barbero": "Lean",
                "fecha": "2026-06-24",
                "hora": "11:00",
                "nombre": "Alice",
            },
        )
        session.commit()

        intake = IntakeService(session, tid)
        result = intake.handle_inbound(
            customer_phone=seeded_session["phone"],
            body="si",
            provider_message_id="compat-002",
            classifier=MockConfirmClassifier(),
        )
        session.commit()

        # Should resolve to "Corte" (the English key wins), not "Manicura".
        assert result["state"] == "booking_confirmed"


class TestDeterministicClassifierUnaffected:
    """The deterministic classifier (no LLM) should not attempt booking —
    it never produces ``booking_confirmed``."""

    def test_greeting_and_menu_still_work(
        self, intake: IntakeService, tenant_id
    ) -> None:
        result = intake.handle_inbound(
            customer_phone="+5491100000001",
            body="hola",
            provider_message_id="det-001",
        )
        assert result["state"] == "awaiting_menu"
        assert result["reply"] == GREETING

    def test_book_selection_advances_state(
        self, intake: IntakeService, tenant_id
    ) -> None:
        intake.handle_inbound(
            customer_phone="+5491100000001",
            body="hola",
            provider_message_id="det-002",
        )
        result = intake.handle_inbound(
            customer_phone="+5491100000001",
            body="1",
            provider_message_id="det-003",
        )
        assert result["state"] == "awaiting_service"
        assert "servicio" in result["reply"].lower()

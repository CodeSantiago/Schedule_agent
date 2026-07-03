"""Deterministic conversational intake.

This is the seam the webhook handler uses to turn an incoming message
into a reply. It is intentionally deterministic and tiny:

1. Greet the customer and put the session into ``awaiting_menu``.
2. On ``1`` → ask for the service (move to ``awaiting_service``).
3. On ``2`` → put the customer into the cancel flow
   (``awaiting_cancellation``).
4. On ``3`` → put the customer into the reschedule flow
   (``awaiting_reschedule``).
5. Anything else → echo the menu.

The default intent classifier is the pure-Python ``classify_intent``
function. A future LLM-backed classifier can drop in behind the
``IntentClassifier`` Protocol in ``packages.application.intake.seam``
without changing the webhook handler.

The intake service is stateless. The webhook handler instantiates one
per request, passing the messaging repositories.

When the conversation reaches ``booking_confirmed`` (the customer has
confirmed the booking), the service attempts to persist the appointment
through ``BookingService.book_slot()`` using the booking data that was
accumulated in the session context during the LLM-driven conversation.
If booking fails (slot taken, past time, barber unavailable, etc.), the
customer receives a friendly error message and the session stays in its
current state so they can retry or start over.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from packages.domain.scheduling.errors import (
    BarberUnavailableError,
    BookingError,
    DateClosedError,
    PastTimeError,
    ServiceRestrictionError,
    SlotTakenError,
)
from packages.infrastructure.repositories.messaging import (
    ConversationSessionRepository,
    IncomingMessageRepository,
    OutgoingMessageRepository,
)


GREETING = (
    "Hola, soy el bot de la barberia. Responde 1 para sacar turno, "
    "2 para cancelar, 3 para reagendar."
)


# Menu options the deterministic classifier understands. Kept here
# (not in the classifier) so the constants are easy to extend without
# touching the intent code.
_MENU_BOOK = "1"
_MENU_CANCEL = "2"
_MENU_RESCHEDULE = "3"

# Greeting patterns — when a message matches any of these the bot
# ALWAYS responds with the configured greeting text, regardless of
# whether the customer is new or returning.
_GREETING_PATTERNS = (
    "hola", "buenas", "buen dia", "buen día", "buenas tardes",
    "buenas noches", "que tal", "qué tal", "como va", "cómo va",
    "como estas", "cómo estás", "hey", "hl", "ola", "saludos",
    "buenass", "holis", "holas",
)

_SESSION_STALE_MINUTES = int(
    os.environ.get("WHATSAPP_SESSION_STALE_MINUTES", "120")
)


def _is_greeting(text: str) -> bool:
    """Return True when ``text`` looks like a greeting.

    Checks the cleaned text against known greeting patterns.
    Matches exact word or word at start of sentence.
    """
    cleaned = text.strip().lower()
    for pat in _GREETING_PATTERNS:
        if cleaned == pat or cleaned.startswith(pat + " ") or cleaned.startswith(pat + ",") or cleaned.startswith(pat + "."):
            return True
    return False


@dataclass(frozen=True)
class Intent:
    """The result of ``classify_intent``. Pure data."""

    kind: str  # "book" | "cancel" | "reschedule" | "info" | "unknown"
    next_state: str  # the conversation_sessions.state to move to
    reply: str  # the body we will send back to the customer
    extracted: dict  # data extracted by the LLM (aditive merge) — empty for deterministic


def classify_intent(text: str, *, current_state: str = "start") -> Intent:
    """Map a free-form text reply to a deterministic intent + next state.

    The function is pure so it is trivial to test. A future LLM fallback
    can wrap this and add a fuzzy layer without changing callers.

    ``current_state`` lets the same string ("1") mean different things
    in different positions in the funnel (e.g. selecting a service
    code vs. selecting a slot). For Part 3 we only handle the top of
    the funnel.
    """
    cleaned = (text or "").strip()
    # Lowercase comparison, but the menu numbers are unchanged.
    if current_state in ("start", "awaiting_menu", "idle"):
        if cleaned == _MENU_BOOK:
            return Intent(
                kind="book",
                next_state="awaiting_service",
                reply=(
                    "Perfecto. Decime el servicio: 1 Corte, 2 Barba, 3 Corte y Barba."
                ),
                extracted={},
            )
        if cleaned == _MENU_CANCEL:
            return Intent(
                kind="cancel",
                next_state="awaiting_cancellation",
                reply=(
                    "Listo. Decime el nombre con quien sacaste el turno y lo cancelo."
                ),
                extracted={},
            )
        if cleaned == _MENU_RESCHEDULE:
            return Intent(
                kind="reschedule",
                next_state="awaiting_reschedule",
                reply=(
                    "Dale. Decime el nombre y la fecha del turno que queres mover."
                ),
                extracted={},
            )
    return Intent(
        kind="unknown",
        next_state=current_state,
        reply=(
            "No te entiendo todavia. Responde 1 para sacar turno, "
            "2 para cancelar, 3 para reagendar."
        ),
        extracted={},
    )


# ── Backward compat: old Spanish state values → English ──────────────────

# Map of legacy Spanish session state values to English. Used so that
# sessions persisted before the state-name normalization still work.
_SPANISH_TO_ENGLISH_STATES: dict[str, str] = {
    "inicio": "start",
    "esperando_menu": "awaiting_menu",
    "esperando_servicio": "awaiting_service",
    "esperando_dia": "awaiting_day",
    "esperando_barbero": "awaiting_barber",
    "esperando_horario": "awaiting_time",
    "esperando_nombre": "awaiting_name",
    "confirmacion_turno": "booking_confirmation",
    "turno_confirmado": "booking_confirmed",
    "esperando_cancelacion": "awaiting_cancellation",
    "turno_cancelado": "booking_cancelled",
    "esperando_reagendar": "awaiting_reschedule",
    "seleccion_turno_cancelar": "selecting_cancel_appointment",
    "seleccion_turno_reagendar": "selecting_reschedule_appointment",
    "seleccion_nuevo_horario": "selecting_new_time",
    "turno_reagendado": "booking_rescheduled",
}


def _normalize_session_state(state: str) -> str:
    """Translate old Spanish state values to English on read.

    Called after ``find_or_create`` so that already-persisted sessions
    with legacy state values are transparently mapped to the new
    English names. Unknown values are returned as-is.
    """
    return _SPANISH_TO_ENGLISH_STATES.get(state, state)


class IntakeService:
    """Stateless orchestrator used by the webhook handler.

    The handler is responsible for committing the session; this
    service only flushes. The split mirrors the rest of the
    application layer.
    """

    def __init__(self, session: Session, tenant_id: UUID) -> None:
        self._session = session
        self._tenant_id = tenant_id
        self._sessions = ConversationSessionRepository(session, tenant_id)
        self._incoming = IncomingMessageRepository(session, tenant_id)
        self._outgoing = OutgoingMessageRepository(session, tenant_id)

    @property
    def session(self) -> Session:
        return self._session

    def _session_is_stale(self, conv_session: Any) -> bool:
        """Return True when the persisted session is too old to trust.

        Stale sessions should not leak old booking context (barber, date,
        service, etc.) into a new conversation started hours later.
        """
        if _SESSION_STALE_MINUTES <= 0:
            return False

        updated_at = getattr(conv_session, "updated_at", None) or getattr(
            conv_session, "created_at", None
        )
        if updated_at is None:
            return False
        if getattr(updated_at, "tzinfo", None) is not None:
            updated_at = updated_at.replace(tzinfo=None)
        cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(
            minutes=_SESSION_STALE_MINUTES
        )
        return updated_at <= cutoff

    def _reset_session(self, conv_session: Any) -> None:
        """Reset a stale conversation in place.

        We keep the same row/customer binding, but clear the transient
        booking draft so a new greeting or booking request starts from a
        clean slate instead of reusing old extracted values.
        """
        conv_session.state = "start"
        conv_session.context = {}
        conv_session.last_message_seq = 0
        self._session.flush()

    def _try_book_appointment(
        self,
        *,
        customer_phone: str,
        context: dict[str, Any],
    ) -> tuple[bool, str]:
        """Attempt to persist an appointment from accumulated session context.

        Called when the conversation reaches ``booking_confirmed``. Resolves
        service/barber names from the session context to IDs and delegates
        to ``BookingService.book_slot()`` so all domain rules (slot grid,
        haircut-only, absences, double-booking) are enforced.

        Returns ``(True, "")`` on success. On failure returns
        ``(False, "customer-facing error message")`` and leaves the session
        unchanged so the customer can retry.
        """
        # ── Normalize legacy Spanish session keys for backward compat ────
        context = _normalize_session_context(context)

        # ── Extract booking fields from accumulated context ──────────────
        service_name = (context.get("service") or "").strip()
        barber_name = (context.get("barber") or "").strip()
        date_str = (context.get("date") or "").strip()
        time_str = (context.get("time") or "").strip()
        customer_name = (context.get("name") or "").strip()

        if not all([service_name, barber_name, date_str, time_str, customer_name]):
            return False, (
                "Faltan datos para agendar el turno. Por favor empezá de nuevo "
                "enviando 'Hola'."
            )

        # ── Resolve service and barber names to DB IDs ───────────────────
        from packages.infrastructure.repositories import (
            BarberRepository,
            ServiceRepository,
        )

        svc_repo = ServiceRepository(self._session, self._tenant_id)
        bar_repo = BarberRepository(self._session, self._tenant_id)

        services = svc_repo.list_active()
        barbers = bar_repo.list_active()

        service = _match_by_name(services, service_name, "name")
        barber = _match_by_name(barbers, barber_name, "name")

        if service is None:
            return False, f"No encontré el servicio '{service_name}'."
        if barber is None:
            return False, f"No encontré al barbero '{barber_name}'."

        # ── Parse date and time from extracted strings ──────────────────
        try:
            start_at = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        except (ValueError, TypeError):
            return False, "La fecha u hora no son válidas. Por favor empezá de nuevo."

        # ── Delegate to the booking service ─────────────────────────────
        from packages.application.scheduling.booking_service import (
            BookSlotCommand,
            BookingService,
        )

        booking_svc = BookingService(self._session, self._tenant_id)
        try:
            booking_svc.book_slot(
                BookSlotCommand(
                    tenant_id=self._tenant_id,
                    barber_id=barber.id,
                    service_id=service.id,
                    start_at=start_at,
                    customer_name=customer_name,
                    customer_phone=customer_phone,
                    notes="Agendado vía WhatsApp",
                )
            )
            return True, ""
        except SlotTakenError:
            return False, (
                "Ese horario ya está reservado. Por favor elegí otro horario "
                "respondiendo con una hora distinta."
            )
        except PastTimeError:
            return False, (
                "Esa hora ya pasó. Por favor elegí otro horario "
                "respondiendo con una hora distinta."
            )
        except (BarberUnavailableError, DateClosedError):
            return False, (
                "El barbero no está disponible en ese horario. Por favor "
                "elegí otro horario."
            )
        except ServiceRestrictionError:
            return False, (
                "Ese servicio no está disponible en ese horario. Por favor "
                "elegí otro horario."
            )
        except BookingError:
            return False, (
                "No se pudo agendar el turno. Por favor empezá de nuevo "
                "enviando 'Hola'."
            )

    def _get_greeting(self) -> str:
        """Read the tenant's configured greeting, or fall back to the default."""
        from packages.infrastructure.repositories import TenantRepository

        try:
            repo = TenantRepository(self._session, self._tenant_id)
            settings = repo.get_settings()
            if settings and settings.config:
                bot = settings.config.get("bot", {}) or {}
                greeting = (bot.get("greeting_text") or "").strip()
                if greeting:
                    return greeting
        except Exception:
            pass
        return GREETING

    def _build_llm_context(
        self,
        conv_session: Any,
    ) -> dict[str, Any]:
        """Build the context dict for the LLM classifier.

        Gathers tenant info, services, barbers, business hours, session
        data, and conversation history. Returns a flat dict that the
        classifier will inject into its dynamic prompt.
        """
        from packages.infrastructure.repositories import (
            BarberRepository,
            ServiceRepository,
            TenantRepository,
        )
        from packages.infrastructure.db.models.tenants import Tenant

        ctx: dict[str, Any] = {}

        # ── Tenant info ─────────────────────────────────────────────
        tenant = self._session.get(Tenant, self._tenant_id)
        if tenant:
            ctx["tenant_name"] = tenant.name
            # Prefer business.location from config, fall back to tenant.location
            biz_loc = ""
            try:
                settings = TenantRepository(self._session, self._tenant_id).get_settings()
                if settings and settings.config:
                    biz = settings.config.get("business", {}) or {}
                    biz_loc = (biz.get("location") or "").strip()
            except Exception:
                pass
            ctx["location"] = biz_loc or (tenant.location or "")

        # ── Services ────────────────────────────────────────────────
        try:
            svc_repo = ServiceRepository(self._session, self._tenant_id)
            svc_rows = svc_repo.list_active()
            ctx["services"] = [
                {
                    "name": s.name,
                    "price_cents": getattr(s, "price_cents", 0) or 0,
                    "duration_min": getattr(s, "duration_minutes", 30) or 30,
                }
                for s in svc_rows
            ]
        except Exception:
            ctx["services"] = []

        # ── Barbers ─────────────────────────────────────────────────
        try:
            bar_repo = BarberRepository(self._session, self._tenant_id)
            bar_rows = bar_repo.list_active()
            ctx["barbers"] = [
                {
                    "name": b.name,
                    "available_days": _barber_days_summary(b),
                }
                for b in bar_rows
            ]
        except Exception:
            ctx["barbers"] = []

        # ── Business hours ──────────────────────────────────────────
        try:
            t_repo = TenantRepository(self._session, self._tenant_id)
            settings = t_repo.get_settings()
            if settings and settings.config:
                biz = settings.config.get("business", {}) or {}
                ctx["business_hours"] = (biz.get("hours") or "").strip()
        except Exception:
            pass

        # ── Session data (normalize legacy Spanish keys) ────────────
        ctx["session_data"] = _normalize_session_context(
            dict(conv_session.context or {})
        )

        # ── Conversation history (last 6 messages) ──────────────────
        try:
            incoming_list = self._incoming.list_for_session(conv_session.id)[-6:]
            outgoing_list = self._outgoing.list_for_session(conv_session.id)[-6:]
            ctx["history"] = _build_history(incoming_list, outgoing_list, limit=6)
        except Exception:
            ctx["history"] = []

        # ── Timestamp ───────────────────────────────────────────────
        ctx["datetime_now"] = datetime.now().strftime("%Y-%m-%d %H:%M")

        return ctx

    def handle_inbound(
        self,
        *,
        customer_phone: str,
        body: str,
        provider_message_id: str,
        channel: str = "whatsapp",
        raw_payload: dict[str, Any] | None = None,
        classifier: Optional["IntentClassifier"] = None,
    ) -> dict[str, Any]:
        """Process one inbound webhook hit. Returns a small dict with
        the persisted state and the reply that should be sent to the
        customer.

        Idempotent on ``provider_message_id`` — a retried delivery
        returns the original reply without doing any new work and
        with ``duplicate=True``.
        """
        # 1. Resolve the session (create if the customer is new).
        conv_session, _ = self._sessions.find_or_create(
            customer_phone=customer_phone, channel=channel
        )
        # Normalize legacy Spanish session state on read so already-persisted
        # sessions continue to work after the state-name migration.
        conv_session.state = _normalize_session_state(conv_session.state)
        if self._session_is_stale(conv_session):
            self._reset_session(conv_session)

        # 2. Record the incoming message (idempotent on provider id).
        # If the row already existed, the second tuple element is False
        # — we are looking at a webhook retry, not a new message.
        incoming, was_new = self._incoming.record(
            provider_message_id=provider_message_id,
            from_phone=customer_phone,
            body=body,
            channel=channel,
            session_id=conv_session.id,
            raw_payload=raw_payload,
        )
        if not was_new:
            # Webhook retry. Replay the most recent outgoing message
            # for this session, if any. We do not advance the
            # session, do not bump ``last_message_seq``, do not write a
            # second outgoing row. The provider sees the same reply
            # it already saw (or a noop if the original reply has
            # not been written yet — e.g. a delivery that arrived
            # between ``find_or_create`` and ``record``).
            prior = self._outgoing.list_for_session(conv_session.id)
            last_out = prior[-1] if prior else None
            return {
                "session_id": str(conv_session.id),
                "incoming_id": str(incoming.id),
                "outgoing_id": str(last_out.id) if last_out is not None else None,
                "state": conv_session.state,
                "reply": last_out.body if last_out is not None else "",
                "duplicate": True,
            }
        return self.process_recorded_inbound(
            conv_session=conv_session,
            incoming=incoming,
            body=body,
            classifier=classifier,
        )

    def process_recorded_inbound(
        self,
        *,
        conv_session: Any,
        incoming: Any,
        body: str,
        classifier: Optional["IntentClassifier"] = None,
        persist_outgoing: bool = True,
    ) -> dict[str, Any]:
        """Process an inbound row that was already recorded.

        This is the core state-machine step shared by the normal inline
        webhook path and the burst-coalesced background path.
        """
        incoming.status = "processed"
        self._session.flush()

        # 3. Compute the reply.
        #    - Greetings ALWAYS get the configured greeting text,
        #      no matter if the customer is new or returning.
        #    - First-time customers (last_message_seq == 0) also
        #      always get the greeting.
        #    - Everything else goes through the classifier (LLM or
        #      deterministic). If the LLM classifier raises, we
        #      fall back to the deterministic classifier so the bot
        #      never goes silent.
        if conv_session.last_message_seq == 0 or _is_greeting(body):
            reply_body = self._get_greeting()
            new_state = "awaiting_menu"
            extracted_update: dict[str, Any] = {}
        else:
            if classifier is not None:
                try:
                    # Build the LLM context (services, barbers, history …)
                    llm_ctx = self._build_llm_context(conv_session)
                    intent = classifier.classify(
                        body,
                        current_state=conv_session.state,
                        context=llm_ctx,
                    )
                except Exception:
                    # LLM failed (rate limit, timeout, etc.) → fall back
                    # to deterministic so the bot never goes silent.
                    intent = classify_intent(
                        body, current_state=conv_session.state
                    )
            else:
                intent = classify_intent(body, current_state=conv_session.state)
            reply_body = intent.reply
            new_state = intent.next_state
            extracted_update = intent.extracted

        # 3b. When the customer confirms a booking (booking_confirmed),
        #     actually persist the appointment through the booking service.
        #     If validation fails, keep the current state and reply with a
        #     friendly error so the customer can retry.
        if new_state == "booking_confirmed":
            merged_ctx = _normalize_session_context(
                dict(conv_session.context or {})
            )
            merged_ctx.update(extracted_update)
            success, err_msg = self._try_book_appointment(
                customer_phone=conv_session.customer_phone,
                context=merged_ctx,
            )
            if not success:
                reply_body = err_msg
                new_state = conv_session.state
                extracted_update = {}

        # 4. Merge extracted data into the session context (aditive).
        context_patch: dict[str, Any] = {"last_intent_kind": _intent_kind_for(reply_body)}
        if extracted_update:
            # Normalize legacy Spanish keys so the merge does not
            # double-store data under old and new keys.
            existing = _normalize_session_context(
                dict(conv_session.context or {})
            )
            existing.update(extracted_update)
            context_patch.update(extracted_update)

        # 5. Advance the session + append the outgoing audit row.
        self._sessions.advance(
            conv_session,
            new_state=new_state,
            context_patch=context_patch,
        )
        outgoing = None
        if persist_outgoing:
            outgoing = self._outgoing.record(
                to_phone=conv_session.customer_phone,
                body=reply_body,
                session_id=conv_session.id,
                status="sent",
            )

        return {
            "session_id": str(conv_session.id),
            "incoming_id": str(incoming.id),
            "outgoing_id": str(outgoing.id) if outgoing is not None else None,
            "state": new_state,
            "reply": reply_body,
            "duplicate": False,
        }


# ── Session context key normalization ─────────────────────────────────────


# Map of legacy Spanish session context keys to English. Used for backward
# compatibility: old sessions persisted before the normalization store keys
# like "servicio" instead of "service". The shim normalizes on read so
# existing sessions continue to function without a DB migration.
_SPANISH_TO_ENGLISH_KEYS: dict[str, str] = {
    "servicio": "service",
    "barbero": "barber",
    "fecha": "date",
    "hora": "time",
    "nombre": "name",
    "turno_id": "appointment_id",
}


def _normalize_session_context(context: dict[str, object]) -> dict[str, object]:
    """Map legacy Spanish session context keys to English.

    Old sessions persisted before the normalization still use Spanish keys
    in the ``context`` JSONB column. This function normalizes them on read
    so existing sessions continue to work without a DB migration.

    If both the old Spanish key and the new English key are present, the
    English value takes priority (newer data wins).
    """
    out: dict[str, object] = {}
    # Copy non-mapped keys through as-is.
    for k, v in context.items():
        if k not in _SPANISH_TO_ENGLISH_KEYS:
            out[k] = v
    # Apply mapping: prefer English, fall back to Spanish.
    for old_key, new_key in _SPANISH_TO_ENGLISH_KEYS.items():
        if new_key in context:
            out[new_key] = context[new_key]
        elif old_key in context:
            out[new_key] = context[old_key]
    return out


# ── LLM context helpers ──────────────────────────────────────────────────


def _barber_days_summary(barber: Any) -> str:
    """Return a human-readable availability summary for a barber.

    Tries to read the barber's schedules (lazy-loaded relationship);
    returns an empty string when no schedules are found.
    """
    try:
        schedules = getattr(barber, "schedules", None)
        if schedules is None:
            return ""
        days: list[str] = []
        weekday_names = {
            "mon": "lun", "tue": "mar", "wed": "mié",
            "thu": "jue", "fri": "vie", "sat": "sáb", "sun": "dom",
        }
        seen_raw: list[str] = []
        for s in schedules:
            raw = getattr(s, "weekday", "")
            if raw and raw not in seen_raw:
                seen_raw.append(raw)
                days.append(weekday_names.get(raw, raw))
        if not days:
            return ""
        if len(days) <= 3:
            return ", ".join(days)
        # Compact range when the list is longer.
        return f"{days[0]} a {days[-1]}"
    except Exception:
        return ""


def _build_history(
    incoming: list[Any],
    outgoing: list[Any],
    *,
    limit: int = 6,
) -> list[dict[str, str]]:
    """Interleave recent incoming + outgoing messages into a chat history.

    Returns a list of ``{"role": …, "text": …}`` dicts ordered by time,
    limited to ``limit`` entries total.
    """
    events: list[tuple[int, str, str]] = []

    for msg in outgoing:
        ts = getattr(msg, "created_at", None)
        order = ts.timestamp() if hasattr(ts, "timestamp") else 0
        events.append((order, "bot", getattr(msg, "body", "")))

    for msg in incoming:
        ts = getattr(msg, "created_at", None)
        order = ts.timestamp() if hasattr(ts, "timestamp") else 0
        events.append((order, "customer", getattr(msg, "body", "")))

    # Sort by timestamp ascending, take the last N.
    events.sort(key=lambda e: e[0])
    recent = events[-limit:]

    return [{"role": role, "text": text} for _, role, text in recent]


def _match_by_name(rows: list, name: str, attr: str) -> Any:
    """Find the first row whose ``attr`` matches ``name`` case-insensitively.

    Returns ``None`` when no row matches.
    """
    cleaned = name.strip().lower()
    for r in rows:
        if (getattr(r, attr, "") or "").strip().lower() == cleaned:
            return r
    return None


def _intent_kind_for(reply: str) -> str:
    """Coarse label for the ``context.last_intent_kind`` field.

    Used by dashboards / debuggers; not a routing key. Maps the reply
    body to one of a small set of stable labels.
    """
    if reply.startswith("Hola"):
        return "greeting"
    if "servicio" in reply:
        return "ask_service"
    if "cancel" in reply.lower():
        return "ask_cancel"
    if "mover" in reply.lower() or "reagendar" in reply.lower():
        return "ask_reschedule"
    return "unknown"


def build_intent_classifier(
    *,
    api_key: str | None = None,
    model: str | None = None,
    http_client: Any = None,
) -> "IntentClassifier":
    """Build the best available intent classifier from environment config.

    When ``OPENROUTER_API_KEY`` is set (env or argument), returns an
    ``OpenRouterIntentClassifier``.  Otherwise returns a
    ``DeterministicIntentClassifier`` so the webhook never crashes
    just because no LLM key is present.
    """
    effective_key = api_key or os.environ.get("OPENROUTER_API_KEY") or ""
    if effective_key:
        from packages.infrastructure.llm.openrouter import (
            OpenRouterIntentClassifier,
        )

        return OpenRouterIntentClassifier(
            api_key=effective_key, model=model, http_client=http_client
        )
    return DeterministicIntentClassifier()


# Re-export the LLM intent seam so callers do not have to know the
# sub-module path.
from packages.application.intake.seam import (  # noqa: E402
    DeterministicIntentClassifier,
    IntentClassifier,
)

__all__ = [
    "GREETING",
    "IntakeService",
    "Intent",
    "IntentClassifier",
    "DeterministicIntentClassifier",
    "classify_intent",
    "build_intent_classifier",
]

"""WhatsApp-like webhook endpoint (per-tenant) — production hardened.

This is the thin seam a real provider (Kapso, Twilio, 360dialog) will
hit when a customer sends a message. The flow:

1. The provider hits ``POST /webhooks/whatsapp/{tenant_id}`` with a
   Meta WhatsApp Cloud API-shaped payload (``entry`` → ``changes`` →
   ``value`` → ``messages[]``).  The handler parses this format
   directly from the raw JSON body, plus two additional formats:
   the Kapso-native format and a simplified test-friendly format.

2. If the tenant has an active ``whatsapp`` provider config with a
   ``webhook_secret``, the signature header is validated via
   HMAC-SHA256.

3. The handler resolves a ``MessageTransport`` and an
   ``IntentClassifier`` from the tenant's provider configs.

4. **Input sanitization**: the message body is sanitised (control
   chars stripped, length limited) before reaching the LLM.

5. **Idempotency check**: a content-based SHA-256 dedup key is
   checked against the ``idempotency_keys`` table within a sliding
   time window to suppress burst duplicates.

6. **Per-conversation locking**: an advisory lock is acquired for
   ``(tenant, customer_phone)`` so multi-worker environments do not
   interleave two messages from the same customer.

7. **Jailbreak detection**: messages matching known prompt-injection
   patterns are logged and rejected gracefully.

8. ``IntakeService.handle_inbound`` processes the message, creates
   or advances the conversation session, and appends an
   ``outgoing_messages`` row with the reply body.

9. The transport ``send()`` is called (with retry) and the result
   is stamped on the outgoing row.

The endpoint intentionally returns ``200`` on every well-formed
request (even processing failures) so providers stop retrying.  Only
truly unexpected errors (missing tenant, bad payload shape) propagate
as 4xx.
"""

from __future__ import annotations

import os
import json
from typing import Any
import uuid as _uuid
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from apps.api.src.deps import get_session
from apps.api.src.schemas import WebhookInboundResult
from packages.application.intake import IntakeService
from packages.application.intake.seam import IntentClassifier
from packages.application.messaging import (
    MessageTransport,
    TransportFactory,
)
from packages.infrastructure.llm import LLMClassifierFactory
from packages.application.providers.kapso_config import (
    verify_webhook_signature,
)
from packages.infrastructure.db.models.tenants import Tenant

router = APIRouter(prefix="/webhooks/whatsapp", tags=["webhooks"])

# ── Dedup window (seconds) ────────────────────────────────────────────────────
# Messages from the same customer with identical body content within this
# window are treated as duplicates.
_DEDUP_WINDOW_SECONDS: int = 10

# ── Lock TTL (seconds) ────────────────────────────────────────────────────────
# How long a per-conversation lock stays valid. If a message takes longer
# than this, the lock expires and another worker can pick it up.
_LOCK_TTL_SECONDS: int = 30

# ── Transport send retries ────────────────────────────────────────────────────
_SEND_RETRIES: int = 2
_SEND_RETRY_DELAY_MS: int = 500
_BURST_COALESCE_ENABLED: bool = os.environ.get(
    "WHATSAPP_BURST_COALESCE_ENABLED", "false"
).strip().lower() in ("1", "true", "yes", "on")
_BURST_WINDOW_SECONDS: float = float(
    os.environ.get("WHATSAPP_BURST_WINDOW_SECONDS", "1.2")
)

# ── Redis key prefixes ────────────────────────────────────────────────────────
_REDIS_DEDUP_PREFIX = "whatsapp:dedup"
_REDIS_LOCK_PREFIX = "whatsapp:lock"
_REDIS_BURST_PREFIX = "whatsapp:burst"
_REDIS_PENDING_REPLY_PREFIX = "whatsapp:pending-reply"


def _extract_message(
    body: dict[str, Any],
) -> dict[str, Any] | None:
    """Extract the first inbound message from any supported payload format.

    Supported formats (checked in order):

    1. **Simplified format** (test-friendly)::

        {"provider_message_id": "…", "from_phone": "…", "body": "…"}

    2. **Kapso-native format** (keys: ``message``, ``conversation``)::

        {"message": {"id": "…", "from": "…", "text": {"body": "…"}, "type": "text"}, …}

    3. **Meta WhatsApp Cloud API format** (keys: ``entry``, ``object``)::

        {"entry": [{"changes": [{"value": {"messages": […]}}]}], …}

    Returns a flat dict with ``from_phone``, ``body``, ``provider_message_id``,
    and ``raw_payload``, or ``None`` when this is not a message event.
    """
    msg: dict[str, Any] | None = None

    # ── 1. Simplified format ──────────────────────────────────────────────
    if "provider_message_id" in body and "from_phone" in body:
        return {
            "from_phone": body.get("from_phone", ""),
            "body": body.get("body", ""),
            "provider_message_id": body.get("provider_message_id", ""),
            "raw_payload": body,
        }

    # ── 2. Kapso-native format ────────────────────────────────────────────
    if "message" in body:
        msg = body["message"]
    # ── 3. Meta WhatsApp Cloud API format ─────────────────────────────────
    else:
        try:
            entry = body.get("entry", [])
            if entry:
                changes = entry[0].get("changes", [])
                if changes:
                    value = changes[0].get("value", {})
                    messages = value.get("messages", [])
                    if messages:
                        msg = messages[0]
        except (IndexError, KeyError, TypeError):
            pass

    if msg is None:
        return None

    msg_type = msg.get("type", "text")
    text_body = ""
    if msg_type == "text":
        text_body = msg.get("text", {}).get("body", "")
    elif msg_type == "interactive":
        interactive = msg.get("interactive", {})
        ir = interactive.get("button_reply") or interactive.get("list_reply") or {}
        text_body = ir.get("title") or ir.get("id") or ""
    else:
        text_body = (
            msg.get("text", {}).get("body", "")
            if isinstance(msg.get("text"), dict)
            else ""
        )

    return {
        "from_phone": msg.get("from", ""),
        "body": text_body,
        "provider_message_id": msg.get("id", ""),
        "raw_payload": body,
    }


# ── Helpers ─────────────────────────────────────────────────────────────────


def _resolve_transport(session: Session, tenant_id: UUID) -> MessageTransport:
    """Resolve the right transport for a tenant."""
    factory = TransportFactory(lambda: session)
    return factory.for_tenant(tenant_id)


def _resolve_classifier(session: Session, tenant_id: UUID) -> IntentClassifier:
    """Resolve the right ``IntentClassifier`` for a tenant."""
    factory = LLMClassifierFactory(lambda: session)
    return factory.for_tenant(tenant_id)


def _load_webhook_secret(session: Session, tenant_id: UUID) -> str | None:
    """Read the webhook secret from the tenant's active WhatsApp config."""
    from packages.infrastructure.repositories import ProviderConfigRepository

    repo = ProviderConfigRepository(session, tenant_id)
    active = repo.get_active_for_kind("whatsapp")
    if active is None:
        return None
    return (active.credentials or {}).get("webhook_secret") or None


def _send_with_retry(
    transport: MessageTransport,
    *,
    to_phone: str,
    body: str,
    session_id: UUID | None = None,
    max_retries: int = _SEND_RETRIES,
    retry_delay_ms: int = _SEND_RETRY_DELAY_MS,
) -> tuple[bool, str | None, str | None]:
    """Send a message with retry on transient failures.

    Returns ``(delivered, provider_message_id, error)``.
    """
    import time as _time

    for attempt in range(max_retries + 1):
        result = transport.send(
            to_phone=to_phone,
            body=body,
            session_id=session_id,
        )
        if result.delivered:
            return True, result.provider_message_id, None
        if attempt < max_retries and result.error:
            _time.sleep(retry_delay_ms / 1000.0)
    return False, None, result.error if result else "send failed"


def _acquire_conversation_lock(
    session: Session,
    tenant_id: UUID,
    customer_phone: str,
    *,
    ttl_seconds: int = _LOCK_TTL_SECONDS,
) -> tuple[bool, str | None, str]:
    """Acquire a per-conversation advisory lock.

    Returns ``(acquired, backend, token)`` where backend is ``redis`` or
    ``db`` and token is the Redis lock token when Redis is used.
    """
    from packages.infrastructure.redis import get_redis
    from packages.infrastructure.repositories.messaging import (
        ConversationLockRepository,
    )

    redis = get_redis()
    lock_key = f"{_REDIS_LOCK_PREFIX}:{tenant_id}:{customer_phone}"
    token = _uuid.uuid4().hex
    if redis.is_available() and redis.set(lock_key, token, ex=ttl_seconds, nx=True):
        return True, "redis", token

    lock_repo = ConversationLockRepository(session, tenant_id)
    lock, acquired = lock_repo.acquire(
        customer_phone,
        ttl_seconds=ttl_seconds,
    )
    return acquired, "db", token if acquired else ""


def _release_conversation_lock(
    session: Session,
    tenant_id: UUID,
    customer_phone: str,
    *,
    backend: str,
    token: str,
) -> bool:
    """Release the per-conversation lock."""
    if backend == "redis":
        from packages.infrastructure.redis import get_redis

        redis = get_redis()
        return redis.compare_and_delete(
            f"{_REDIS_LOCK_PREFIX}:{tenant_id}:{customer_phone}", token
        )

    from packages.infrastructure.repositories.messaging import (
        ConversationLockRepository,
    )

    lock_repo = ConversationLockRepository(session, tenant_id)
    return lock_repo.release(customer_phone)


def _record_dedup_key(
    session: Session,
    tenant_id: UUID,
    *,
    idempotency_key: str,
    customer_phone: str,
    body_hash: str,
    provider_message_id: str,
    ttl_seconds: int,
) -> tuple[bool, str]:
    """Record dedup key using Redis when available, DB otherwise.

    Returns ``(is_new, backend)``.
    """
    from packages.infrastructure.redis import get_redis
    from packages.infrastructure.repositories.messaging import IdempotencyKeyRepository

    redis = get_redis()
    if redis.is_available() and redis.set(
        f"{_REDIS_DEDUP_PREFIX}:{tenant_id}:{idempotency_key}",
        provider_message_id,
        ex=ttl_seconds,
        nx=True,
    ):
        return True, "redis"
    if redis.is_available():
        return False, "redis"

    dedup_repo = IdempotencyKeyRepository(session, tenant_id)
    _, is_new = dedup_repo.record(
        idempotency_key=idempotency_key,
        customer_phone=customer_phone,
        body_hash=body_hash,
        provider_message_id=provider_message_id,
        ttl_seconds=ttl_seconds,
    )
    return is_new, "db"


def _deliver_outgoing_message(
    *,
    bind: Any,
    tenant_id: UUID,
    outgoing_id: str,
    customer_phone: str,
    reply: str,
    session_id: str | None,
) -> None:
    """Background boundary for transport sends.

    Opens a fresh DB session, resolves the tenant transport, sends the
    message with retry, and updates the outgoing row status.
    """
    from sqlalchemy.orm import sessionmaker

    from packages.infrastructure.db.models.messaging import OutgoingMessage

    session_factory = sessionmaker(
        bind=bind,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    bg = session_factory()
    try:
        transport = _resolve_transport(bg, tenant_id)
        outgoing = bg.get(OutgoingMessage, UUID(outgoing_id))
        if outgoing is None:
            bg.rollback()
            return

        delivered, provider_msg_id, error = _send_with_retry(
            transport,
            to_phone=customer_phone,
            body=reply,
            session_id=UUID(session_id) if session_id else None,
        )
        if provider_msg_id:
            outgoing.provider_message_id = provider_msg_id
        outgoing.status = "sent" if delivered else "failed"
        bg.commit()
    except Exception:
        try:
            bg.rollback()
        except Exception:
            pass
    finally:
        bg.close()


def _background_bind_from_session(session: Session) -> Any:
    """Return a stable bind for background work.

    In request handlers, ``session.get_bind()`` may be a live Connection.
    Background tasks should use the underlying Engine when available so they
    open their own independent transactional session.
    """
    bind = session.get_bind()
    return getattr(bind, "engine", bind)


def _process_burst_window(
    *,
    bind: Any,
    tenant_id: UUID,
    customer_phone: str,
    channel: str = "whatsapp",
    schedule_token: str | None = None,
) -> None:
    """Process a burst window of queued inbound messages for one customer.

    Reads all not-yet-processed inbound rows for the session in arrival
    order, advances the state machine for each one, and only persists/sends
    the final reply. This turns "2 or 3 quick messages" into one coherent
    bot response while preserving every inbound audit row.
    """
    import logging
        
    from sqlalchemy import select
    from sqlalchemy.orm import sessionmaker

    from packages.infrastructure.db.models.messaging import IncomingMessage

    logger = logging.getLogger(__name__)
    session_factory = sessionmaker(
        bind=bind,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    bg = session_factory()
    lock_backend = "db"
    lock_token = ""
    try:
        acquired, lock_backend, lock_token = _acquire_conversation_lock(
            bg, tenant_id, customer_phone
        )
        if not acquired:
            return
        if lock_backend == "db":
            bg.commit()

        intake = IntakeService(bg, tenant_id)
        conv_session = intake._sessions.get_for_customer(customer_phone, channel)
        if conv_session is None:
            bg.rollback()
            return

        rows = list(
            bg.execute(
                select(IncomingMessage)
                .where(IncomingMessage.tenant_id == tenant_id)
                .where(IncomingMessage.session_id == conv_session.id)
                .where(IncomingMessage.status == "received")
                .order_by(IncomingMessage.created_at.asc())
            ).scalars()
        )
        if not rows:
            bg.rollback()
            return

        classifier = _resolve_classifier(bg, tenant_id)
        result: dict[str, Any] | None = None
        last_index = len(rows) - 1
        for idx, row in enumerate(rows):
            result = intake.process_recorded_inbound(
                conv_session=conv_session,
                incoming=row,
                body=row.body,
                classifier=classifier,
                persist_outgoing=(idx == last_index),
            )
            bg.commit()

        if result is not None and result.get("outgoing_id") is not None:
            from packages.infrastructure.queue import get_queue

            get_queue().enqueue(
                _deliver_outgoing_message,
                bind=bind,
                tenant_id=tenant_id,
                outgoing_id=result["outgoing_id"],
                customer_phone=customer_phone,
                reply=result["reply"],
                session_id=result.get("session_id"),
            )
    except Exception:
        logger.exception("Burst-window processing failed for %s", customer_phone)
        try:
            bg.rollback()
        except Exception:
            pass
    finally:
        try:
            from packages.infrastructure.redis import get_redis

            redis = get_redis()
            if schedule_token and redis.is_available():
                redis.compare_and_delete(
                    f"{_REDIS_BURST_PREFIX}:{tenant_id}:{customer_phone}",
                    schedule_token,
                )
        except Exception:
            pass
        try:
            released = _release_conversation_lock(
                bg,
                tenant_id,
                customer_phone,
                backend=lock_backend,
                token=lock_token,
            )
            if lock_backend == "db":
                if released:
                    bg.commit()
                else:
                    bg.rollback()
        except Exception:
            try:
                bg.rollback()
            except Exception:
                pass
        bg.close()


def _queue_coalesced_reply(
    *,
    bind: Any,
    tenant_id: UUID,
    customer_phone: str,
    outgoing_id: str,
    reply: str,
    session_id: str | None,
) -> None:
    """Store the latest reply for a conversation and flush it once.

    Each rapid inbound can overwrite the pending reply payload, but only
    one delayed flush job is scheduled per burst window.
    """
    from packages.infrastructure.queue import get_queue
    from packages.infrastructure.redis import get_redis

    redis = get_redis()
    pending_key = f"{_REDIS_PENDING_REPLY_PREFIX}:{tenant_id}:{customer_phone}"
    schedule_key = f"{_REDIS_BURST_PREFIX}:{tenant_id}:{customer_phone}"
    schedule_token = _uuid.uuid4().hex

    payload = json.dumps(
        {
            "outgoing_id": outgoing_id,
            "reply": reply,
            "session_id": session_id,
        }
    )
    redis.set(pending_key, payload, ex=max(int(_BURST_WINDOW_SECONDS) + 30, 31))
    scheduled = redis.set(
        schedule_key,
        schedule_token,
        ex=max(int(_BURST_WINDOW_SECONDS) + 30, 31),
        nx=True,
    )
    if scheduled:
        get_queue().enqueue_in(
            _BURST_WINDOW_SECONDS,
            _flush_coalesced_reply,
            bind=bind,
            tenant_id=tenant_id,
            customer_phone=customer_phone,
            schedule_token=schedule_token,
        )


def _flush_coalesced_reply(
    *,
    bind: Any,
    tenant_id: UUID,
    customer_phone: str,
    schedule_token: str,
) -> None:
    """Deliver the latest pending reply for a burst window."""
    from packages.infrastructure.redis import get_redis

    redis = get_redis()
    pending_key = f"{_REDIS_PENDING_REPLY_PREFIX}:{tenant_id}:{customer_phone}"
    schedule_key = f"{_REDIS_BURST_PREFIX}:{tenant_id}:{customer_phone}"
    raw = redis.get(pending_key)
    try:
        if not raw:
            return
        payload = json.loads(raw)
        _deliver_outgoing_message(
            bind=bind,
            tenant_id=tenant_id,
            outgoing_id=payload["outgoing_id"],
            customer_phone=customer_phone,
            reply=payload["reply"],
            session_id=payload.get("session_id"),
        )
    finally:
        redis.delete(pending_key)
        redis.compare_and_delete(schedule_key, schedule_token)


# ── Route ───────────────────────────────────────────────────────────────────


@router.get("/{tenant_id}", status_code=status.HTTP_200_OK)
def whatsapp_verify(
    tenant_id: UUID,
    request: Request,
    session: Session = Depends(get_session),
) -> str:
    """WhatsApp Cloud API webhook verification (Meta's challenge flow)."""
    import os

    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    expected_token = os.environ.get(
        "WEBHOOK_VERIFY_TOKEN",
        "barber_agent_verify_2026",
    )

    if mode == "subscribe" and token == expected_token and challenge:
        return challenge

    raise HTTPException(status_code=status.HTTP_FORBIDDEN, detail="Verification failed")


@router.post(
    "/{tenant_id}",
    response_model=WebhookInboundResult,
    status_code=status.HTTP_200_OK,
)
async def whatsapp_inbound(
    tenant_id: UUID,
    request: Request,
    session: Session = Depends(get_session),
) -> WebhookInboundResult:
    """Receive one inbound message and return the reply.

    Accepts raw JSON in Meta WhatsApp Cloud API format and parses the
    first inbound message from the nested structure. Supports the
    Kapso-native format and a simplified test-friendly format.

    Production hardening applied:
    - Input sanitisation (control chars, length limit)
    - Jailbreak prompt-injection detection
    - Content-based burst dedup with time window
    - Per-conversation advisory locking
    - LLM output validation
    - Transport send retry
    """
    import json
    import logging

    _logger = logging.getLogger(__name__)

    # ── Parse raw body ──────────────────────────────────────────────────
    try:
        raw_body: bytes = await request.body()
    except Exception:
        raw_body = b""
    if not raw_body:
        return WebhookInboundResult(
            accepted=False, duplicate=False, state="empty-body", reply="",
        )

    try:
        body: dict[str, Any] = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        _logger.warning("Invalid JSON in webhook body: %s", exc)
        return WebhookInboundResult(
            accepted=False, duplicate=False, state="bad-json", reply="",
        )

    # Remove noisy debug prints; keep a concise log line.
    _logger.debug("Webhook payload keys=%s object=%s", list(body.keys()), body.get("object"))

    # 1. Confirm the tenant exists.
    tenant = session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"tenant {tenant_id} not found",
        )

    # 2. Webhook signature verification (optional).
    webhook_secret = _load_webhook_secret(session, tenant_id)
    if webhook_secret:
        signature_header: str | None = request.headers.get(
            "X-Kapso-Signature"
        ) or request.headers.get("X-Provider-Signature")
        if not verify_webhook_signature(
            raw_body=raw_body,
            signature_header=signature_header,
            webhook_secret=webhook_secret,
        ):
            return WebhookInboundResult(
                accepted=False, duplicate=False, state="", reply="",
            )

    # 3. Check if the bot is enabled for this tenant.
    from packages.infrastructure.repositories import TenantRepository as _TenantRepo

    _settings_repo = _TenantRepo(session, tenant_id)
    _settings = _settings_repo.get_settings()
    _bot_enabled = True
    if _settings and _settings.config:
        _bot_cfg = _settings.config.get("bot", {}) or {}
        _bot_enabled = bool(_bot_cfg.get("enabled", True))
    if not _bot_enabled:
        from packages.infrastructure.repositories import TenantAuditLogRepository

        _audit = TenantAuditLogRepository(session, tenant_id)
        _audit.log(
            event_type="bot_paused",
            level="warn",
            message="Bot rejected an inbound message because bot is paused",
            details={},
        )
        session.commit()
        return WebhookInboundResult(
            accepted=True,
            duplicate=False,
            state="bot-paused",
            reply="The booking bot is currently paused. Please try again later or contact the barbershop directly.",
        )

    # 4. Parse the first inbound message.
    parsed = _extract_message(body)
    if parsed is None:
        return WebhookInboundResult(
            accepted=True, duplicate=False, state="ignored", reply="",
        )

    # ── Outbound callback guard ──────────────────────────────────────────
    if not parsed["from_phone"]:
        return WebhookInboundResult(
            accepted=True, duplicate=False, state="outbound-callback", reply="",
        )

    # 5. **Input sanitization** — strip control chars, limit length.
    from packages.infrastructure.security import (
        compute_body_hash,
        detect_jailbreak,
        sanitize_message,
    )

    raw_body_text = parsed["body"]
    sanitized_body = sanitize_message(raw_body_text)
    customer_phone = parsed["from_phone"]

    # 5a. **Jailbreak detection** — log and reject obvious injection attempts.
    if detect_jailbreak(raw_body_text):
        _logger.warning(
            "Jailbreak attempt detected: phone=%s body=%.100s",
            customer_phone, raw_body_text,
        )
        from packages.infrastructure.repositories import TenantAuditLogRepository

        _audit = TenantAuditLogRepository(session, tenant_id)
        _audit.log(
            event_type="jailbreak_detected",
            level="warn",
            message="Prompt injection attempt blocked",
            details={"body_preview": raw_body_text[:200]},
        )
        session.commit()
        return WebhookInboundResult(
            accepted=True,
            duplicate=False,
            state="jailbreak-blocked",
            reply="No entendí. Por favor elegí 1 para agendar, 2 para cancelar o 3 para reagendar.",
        )

    # 5b. Burst coalescing is implemented at the reply-delivery layer when
    # enabled. We still process and persist inbound messages inline so the
    # conversation state stays consistent, but we delay delivery and only
    # send the latest reply in a short burst window.
    from packages.infrastructure.redis import get_redis

    redis = get_redis()

    # 5c. **Content-based dedup** — burst suppression with time window.
    body_hash = compute_body_hash(customer_phone, sanitized_body)
    dedup_key = f"webhook:{customer_phone}:{body_hash[:16]}"

    is_new, dedup_backend = _record_dedup_key(
        session,
        tenant_id,
        idempotency_key=dedup_key,
        customer_phone=customer_phone,
        body_hash=body_hash,
        provider_message_id=parsed["provider_message_id"],
        ttl_seconds=_DEDUP_WINDOW_SECONDS,
    )

    if not is_new:
        # Same content from same phone within the dedup window — suppress.
        _logger.info(
            "Burst duplicate suppressed: phone=%s key=%s",
            customer_phone, dedup_key,
        )
        return WebhookInboundResult(
            accepted=True,
            duplicate=True,
            state="burst-duplicate",
            reply="",
        )

    # Persist the dedup key before any slower downstream work so retries
    # or rapid bursts are suppressed even if the later processing fails.
    if dedup_backend == "db":
        session.commit()

    # 6. **Per-conversation locking** — serialize processing per customer.
    lock_acquired, lock_backend, lock_token = _acquire_conversation_lock(
        session, tenant_id, customer_phone,
    )
    if not lock_acquired:
        _logger.warning(
            "Could not acquire conversation lock: phone=%s — skipping",
            customer_phone,
        )
        session.commit()
        return WebhookInboundResult(
            accepted=True,
            duplicate=False,
            state="lock-contention",
            reply="",
        )

    # Persist the advisory lock before running the intake flow so a later
    # rollback does not accidentally erase the lock row.
    if lock_backend == "db":
        session.commit()

    try:
        # 7. Resolve classifier.
        classifier = _resolve_classifier(session, tenant_id)

        # 8. Drive the intake service.
        logger = logging.getLogger(__name__)
        intake = IntakeService(session, tenant_id)
        try:
            result = intake.handle_inbound(
                customer_phone=customer_phone,
                body=sanitized_body,
                provider_message_id=parsed["provider_message_id"],
                channel="whatsapp",
                raw_payload=parsed["raw_payload"],
                classifier=classifier,
            )
        except Exception as exc:
            logger.exception("Intake processing failed: %s", exc)
            session.rollback()
            return WebhookInboundResult(
                accepted=False, duplicate=False, state="error", reply="",
            )

        # 9. Queue the transport send through the background boundary.
        outgoing_id = result.get("outgoing_id")
        if outgoing_id is not None:
            from packages.infrastructure.db.models.messaging import OutgoingMessage
            from packages.infrastructure.queue import get_queue

            outgoing = session.get(OutgoingMessage, UUID(outgoing_id))
            if outgoing is not None:
                outgoing.status = "processing"

        session.commit()

        if outgoing_id is not None:
            if _BURST_COALESCE_ENABLED and redis.is_available() and _BURST_WINDOW_SECONDS > 0:
                _queue_coalesced_reply(
                    bind=_background_bind_from_session(session),
                    tenant_id=tenant_id,
                    customer_phone=customer_phone,
                    outgoing_id=outgoing_id,
                    reply=result["reply"],
                    session_id=result.get("session_id"),
                )
            else:
                get_queue().enqueue(
                    _deliver_outgoing_message,
                    bind=_background_bind_from_session(session),
                    tenant_id=tenant_id,
                    outgoing_id=outgoing_id,
                    customer_phone=customer_phone,
                    reply=result["reply"],
                    session_id=result.get("session_id"),
                )

        return WebhookInboundResult(
            accepted=True,
            duplicate=bool(result.get("duplicate", False)),
            state=("burst-queued" if (_BURST_COALESCE_ENABLED and redis.is_available() and _BURST_WINDOW_SECONDS > 0 and outgoing_id is not None) else result["state"]),
            reply=("" if (_BURST_COALESCE_ENABLED and redis.is_available() and _BURST_WINDOW_SECONDS > 0 and outgoing_id is not None) else result["reply"]),
        )
    finally:
        # Always release the conversation lock.
        try:
            released = _release_conversation_lock(
                session,
                tenant_id,
                customer_phone,
                backend=lock_backend,
                token=lock_token,
            )
            if released:
                session.commit()
            else:
                session.rollback()
        except Exception:
            try:
                session.rollback()
            except Exception:
                pass
            _logger.exception("Failed to release conversation lock for %s", customer_phone)

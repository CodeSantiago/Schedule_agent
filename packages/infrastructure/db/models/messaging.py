"""Conversation sessions and message log (incoming + outgoing).

- `conversation_sessions` is the per-customer state container (which
  step of the booking funnel the bot is currently in). This is the
  greenfield replacement for the legacy in-RAM + Redis state.
- `incoming_messages` records every inbound webhook payload. The
  `provider_message_id` is unique per tenant so a webhook retried by
  the provider never produces duplicate work.
- `outgoing_messages` is the outbound audit log (one row per send
  attempt) — useful for debugging, retries, and analytics.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql.sqltypes import Enum as SAEnum

from packages.infrastructure.db.base import Base, CreatedAt, UpdatedAt, UuidFK, UuidPK

if TYPE_CHECKING:
    from packages.infrastructure.db.models.tenants import Tenant


# Conversation funnel states (English). The tuple is the canonical list;
# backward compat for legacy persisted rows is handled by
# ``_SPANISH_TO_ENGLISH_STATES`` and ``_normalize_session_state()`` in
# ``packages.application.intake``.
SESSION_STATE_VALUES = (
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
)
SessionStateEnum: SAEnum = SAEnum(
    *SESSION_STATE_VALUES,
    name="session_state",
    native_enum=True,
    create_type=False,
    create_constraint=True,
)

MESSAGE_STATUS_VALUES = ("received", "processing", "processed", "failed", "sent", "delivered")
MessageStatusEnum: SAEnum = SAEnum(
    *MESSAGE_STATUS_VALUES,
    name="message_status",
    native_enum=True,
    create_type=False,
    create_constraint=True,
)


class ConversationSession(Base):
    """Per-customer funnel state, persisted in the database."""

    __tablename__ = "conversation_sessions"
    __table_args__ = (
        # A customer has at most one active session per channel/phone.
        UniqueConstraint("tenant_id", "channel", "customer_phone", name="uq_session_per_customer"),
    )

    id: Mapped[UuidPK]

    tenant_id: Mapped[UuidFK] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )

    channel: Mapped[str] = mapped_column(String(32), nullable=False, default="whatsapp")
    customer_phone: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    state: Mapped[str] = mapped_column(
        SessionStateEnum, nullable=False, default="start", server_default="start"
    )
    # Free-form scratch context (current draft appointment, last intent, etc.).
    context: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # Optimistic-concurrency hint; not strict, just a debugging aid.
    last_message_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]

    incoming_messages: Mapped[list["IncomingMessage"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        foreign_keys="IncomingMessage.session_id",
    )
    outgoing_messages: Mapped[list["OutgoingMessage"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        foreign_keys="OutgoingMessage.session_id",
    )


class IncomingMessage(Base):
    """Every inbound message we received from a provider webhook."""

    __tablename__ = "incoming_messages"
    __table_args__ = (
        # Webhook retries from the provider must never produce duplicates.
        UniqueConstraint(
            "tenant_id", "provider_message_id", name="uq_incoming_provider_message"
        ),
    )

    id: Mapped[UuidPK]

    tenant_id: Mapped[UuidFK] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    session_id: Mapped[UuidFK] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("conversation_sessions.id", ondelete="CASCADE"),
        nullable=True,
    )

    provider_message_id: Mapped[str] = mapped_column(String(128), nullable=False)
    channel: Mapped[str] = mapped_column(String(32), nullable=False, default="whatsapp")
    from_phone: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Full raw payload from the provider — useful for replay/debugging.
    raw_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    status: Mapped[str] = mapped_column(
        MessageStatusEnum, nullable=False, default="received", server_default="received"
    )

    created_at: Mapped[CreatedAt]

    session: Mapped["ConversationSession | None"] = relationship(
        back_populates="incoming_messages",
        foreign_keys=[session_id],
    )


class OutgoingMessage(Base):
    """Audit log of every outbound message we attempted to send."""

    __tablename__ = "outgoing_messages"

    id: Mapped[UuidPK]

    tenant_id: Mapped[UuidFK] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    session_id: Mapped[UuidFK] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("conversation_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )

    to_phone: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Provider-side id returned by the messaging adapter (if any).
    provider_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    status: Mapped[str] = mapped_column(
        MessageStatusEnum, nullable=False, default="sent", server_default="sent"
    )

    created_at: Mapped[CreatedAt]

    session: Mapped["ConversationSession | None"] = relationship(
        back_populates="outgoing_messages",
        foreign_keys=[session_id],
    )


# ── Production hardening: idempotency, dedup, locking ─────────────────────────


class IdempotencyKey(Base):
    """Idempotency keys for inbound message processing.

    Prevents duplicate processing from rapid retries or burst delivery
    within a configurable time window. Keys expire after the TTL window
    and are pruned periodically.
    """

    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_idempotency_key_per_tenant"
        ),
    )

    id: Mapped[UuidPK]

    tenant_id: Mapped[UuidFK] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )

    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    customer_phone: Mapped[str] = mapped_column(String(32), nullable=False)
    # SHA-256 hash of (customer_phone, body_text) for content-based dedup.
    body_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    created_at: Mapped[CreatedAt]
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class ConversationLock(Base):
    """Per-customer advisory lock for conversation serialization.

    Ensures only one worker processes messages for a given customer at a
    time. Locks expire automatically after ``lock_ttl_seconds``, so a
    crashed worker does not deadlock the customer permanently.

    The lock is "advisory" — callers check and set atomically via a
    unique constraint on ``(tenant_id, customer_phone)``.
    """

    __tablename__ = "conversation_locks"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "customer_phone",
            name="uq_conversation_lock_per_customer",
        ),
    )

    id: Mapped[UuidPK]

    tenant_id: Mapped[UuidFK] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )

    customer_phone: Mapped[str] = mapped_column(String(32), nullable=False)
    worker_id: Mapped[str] = mapped_column(String(64), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    acquired_at: Mapped[CreatedAt]
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

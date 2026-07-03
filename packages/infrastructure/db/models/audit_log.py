"""Tenant-scoped audit trail and operational log entries.

A single table serves both purposes:

- **Audit trail**: records who changed what and when (settings updates,
  status changes, etc.) with actor identity and changed-fields summary.
- **Operational log**: runtime events the tenant or superadmin should be
  aware of (bot paused, booking rejected on closed date, etc.) with a
  human-readable message and optional timing.

The schema is future-proof enough for Sheets/Excel sync, bot-flow
events, and per-event extra data without forcing a separate table per
event type.

Tenant-scoped by construction — every row carries ``tenant_id``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from packages.infrastructure.db.base import Base, CreatedAt, UuidFK, UuidPK


class TenantAuditLog(Base):
    """One row per auditable event or operational log entry.

    Columns
    -------
    tenant_id : UUID
        Every log entry belongs to exactly one tenant.
    event_type : str
        Stable machine-readable key, e.g. ``"settings_updated"``,
        ``"bot_paused"``, ``"booking_closed_date_rejected"``.
    level : str
        Severity: ``"info"``, ``"warn"``, or ``"error"``.
    message : str
        Human-readable summary — displayed in the UI.
    actor_scope : str | None
        ``"superadmin"`` or ``"tenant"``, or ``None`` for system-triggered
        events (e.g. a webhook firing without an authenticated user).
    actor_id : str | None
        The principal UUID as a string, when the actor is known.
    changed_fields : dict | None
        Compact summary of fields that changed (e.g. ``{"bot_enabled":
        false}``), so the UI can show a diff without re-reading settings.
    details : dict
        Flexible bag for future use — provider info, request details,
        etc.  Kept compact; not a data lake.
    duration_ms : int | None
        Wall-clock time in milliseconds, when measurable (e.g. the
        time a webhook processing or settings update took).
    created_at : datetime
        When the event occurred.
    """

    __tablename__ = "tenant_audit_logs"

    id: Mapped[UuidPK]

    tenant_id: Mapped[UuidFK] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    level: Mapped[str] = mapped_column(String(16), nullable=False, default="info")

    # Human-readable summary (displayed in the logs list).
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # Actor information.
    actor_scope: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # What changed (for audit-trail events).
    changed_fields: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Flexible extra data payload (compact).
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    # Timing in milliseconds (nullable when not meaningful).
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[CreatedAt]

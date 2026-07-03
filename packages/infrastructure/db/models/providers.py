"""Tenant-scoped provider configuration (messaging, LLM, data, etc.).

A `provider_configs` row is the wiring layer for one external dependency
in one tenant. Keeping it separate from `tenant_settings` (business config)
makes it easy to rotate credentials or swap providers without touching
business configuration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql.sqltypes import Boolean, Enum as SAEnum

from packages.infrastructure.db.base import Base, CreatedAt, UpdatedAt, UuidFK, UuidPK

if TYPE_CHECKING:
    from packages.infrastructure.db.models.tenants import Tenant


# Provider families the platform knows about. Extend carefully — each value
# typically maps to a dedicated adapter in the infrastructure layer.
PROVIDER_KIND_VALUES = (
    "whatsapp",  # e.g. Kapso, Twilio, 360dialog
    "llm",       # e.g. Groq, OpenAI, Anthropic
    "calendar",  # e.g. Google Calendar, fallback none
    "sheets",    # legacy Google Sheets compatibility
    "sms",       # optional fallback channel
)
ProviderKindEnum: SAEnum = SAEnum(
    *PROVIDER_KIND_VALUES,
    name="provider_kind",
    native_enum=True,
    create_type=False,
    create_constraint=True,
)


class ProviderConfig(Base):
    """How a tenant connects to a specific kind of external provider.

    One tenant may have at most one ACTIVE row per `kind`; multiple
    inactive rows are allowed (kept for history / future reactivation).
    The "at most one active" invariant is enforced by a partial unique
    index on `(tenant_id, kind) WHERE is_active` — the standard
    Postgres pattern. SQLite (>= 3.8) supports partial indexes too, so
    the in-memory test schema is consistent with production.
    """

    __tablename__ = "provider_configs"
    __table_args__ = (
        # Partial unique index: only the ACTIVE row per (tenant, kind)
        # is constrained. `text("is_active")` makes the predicate a
        # plain SQL boolean so Postgres + SQLite both accept it.
        Index(
            "uq_provider_active_per_kind",
            "tenant_id",
            "kind",
            unique=True,
            sqlite_where=text("is_active"),
            postgresql_where=text("is_active"),
        ),
    )

    id: Mapped[UuidPK]

    tenant_id: Mapped[UuidFK] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )

    kind: Mapped[str] = mapped_column(ProviderKindEnum, nullable=False, index=True)
    # Human label: "Kapso prod", "Groq primary", etc.
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    # Adapter identifier inside the kind, e.g. "kapso", "groq", "google_calendar".
    provider_name: Mapped[str] = mapped_column(String(64), nullable=False)

    # Encrypted at the application layer before reaching this column.
    credentials: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # Free-form per-provider settings (webhook URL, model name, sheet id, etc.).
    settings: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]

    tenant: Mapped["Tenant"] = relationship(back_populates="provider_configs")

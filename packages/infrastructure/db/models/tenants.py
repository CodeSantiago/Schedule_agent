"""Tenant and tenant_settings models — the root of multi-tenant isolation.

Every other table carries `tenant_id` and must filter by it. The
`tenants` row is the only one without a `tenant_id` column.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from packages.infrastructure.db.base import (
    Base,
    CreatedAt,
    UpdatedAt,
    UuidFK,
    UuidPK,
)

if TYPE_CHECKING:
    from packages.infrastructure.db.models.providers import ProviderConfig


# Lifecycle status for a tenant account. Native Postgres ENUM.
TenantStatusEnum = (
    "active",
    "suspended",
    "trial",
    "churned",
)


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[UuidPK]
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="trial",
        server_default="trial",
    )
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, default="UTC", server_default="UTC"
    )
    location: Mapped[str | None] = mapped_column(String(200), nullable=True)

    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]

    settings: Mapped["TenantSetting | None"] = relationship(
        back_populates="tenant",
        uselist=False,
        cascade="all, delete-orphan",
    )
    provider_configs: Mapped[list["ProviderConfig"]] = relationship(
        back_populates="tenant",
        cascade="all, delete-orphan",
    )


class TenantSetting(Base):
    """Per-tenant configuration bag. Keep flat; JSON for flexibility."""

    __tablename__ = "tenant_settings"

    tenant_id: Mapped[UuidPK] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    # Free-form key/value blob: business hours defaults, language, branding.
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]

    tenant: Mapped["Tenant"] = relationship(back_populates="settings")

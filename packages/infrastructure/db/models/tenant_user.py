"""Tenant user model — per-tenant user accounts.

Every tenant has zero or more `TenantUser` rows. Each row is a login
identity scoped to one tenant. The email is globally unique so no two
tenants can register the same email address across the platform.

This model is the tenant-level counterpart of `SuperadminUser`: same
password hashing contract, same active/inactive string pattern, but
bound to a tenant via `tenant_id`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from packages.infrastructure.db.base import (
    Base,
    CreatedAt,
    UpdatedAt,
    UuidFK,
    UuidPK,
)

if TYPE_CHECKING:
    from packages.infrastructure.db.models.auth import ApiToken
    from packages.infrastructure.db.models.scheduling import Barber
    from packages.infrastructure.db.models.tenants import Tenant




class TenantUser(Base):
    """A per-tenant user account with login credentials.

    Passwords are hashed with PBKDF2-HMAC-SHA256 via `PasswordHasher`,
    the same scheme used for superadmin accounts, so the hashing
    infrastructure is shared.
    """

    __tablename__ = "tenant_users"

    id: Mapped[UuidPK]

    tenant_id: Mapped[PG_UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    # Hashed password — same PBKDF2 format as SuperadminUser.
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    # Display name for the tenant user (e.g. "Ana García").
    name: Mapped[str] = mapped_column(String(120), nullable=False)

    # Optional profile photo URL for the employee workspace.
    photo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Stored as a short string ("true" / "false") for SQLite compat.
    is_active: Mapped[str] = mapped_column(
        String(8), nullable=False, default="true", server_default="true"
    )

    # RBAC role: owner | admin | staff | viewer.
    role: Mapped[str] = mapped_column(
        String(32), nullable=False, default="admin", server_default="admin"
    )

    # Optional link to a barber identity. When set, the user auto-lands
    # on the barber workspace with their own agenda.
    barber_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("barbers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]

    tenant: Mapped["Tenant"] = relationship()
    barber: Mapped["Barber | None"] = relationship()
    tokens: Mapped[list["ApiToken"]] = relationship(
        back_populates="tenant_user",
        cascade="all, delete-orphan",
    )

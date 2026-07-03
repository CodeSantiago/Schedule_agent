"""Auth models: superadmin users and opaque API tokens.

Two tables, both global (no `tenant_id`):

- `superadmin_users`  — one row per platform owner / superadmin. Holds the
  email and a hashed password. We do not need a role column for Part 3 —
  every superadmin has the same authority. The hashing strategy is
  pluggable (the `PasswordHasher` interface), so we can swap algorithms
  without a migration.
- `api_tokens`  — opaque, random tokens used as the credential for
  every authenticated request. Storing only the SHA-256 of the token
  keeps the database from holding the secret in clear; the client
  receives the raw token on `POST /superadmin/auth/login` and sends it
  back as `Authorization: Bearer <token>`. The `scope` column
  discriminates the kind of principal (`"superadmin"` for Part 3, with
  room for `"tenant_user"` later) and `principal_id` points back at
  the matching row.

The token format is a 32-byte URL-safe random string. Hashing strategy
is PBKDF2-HMAC-SHA256 with a per-row salt — the stdlib `hashlib` is
enough, so we don't need a new third-party dep.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from packages.infrastructure.db.base import Base, CreatedAt, UpdatedAt, UuidPK

if TYPE_CHECKING:
    from packages.infrastructure.db.models.tenant_user import TenantUser


# Token-scope values. The `scope` column is a free string (with a
# server default) so we can add new principal types without another
# migration.
API_TOKEN_SCOPE_SUPERADMIN = "superadmin"
API_TOKEN_SCOPE_TENANT = "tenant"


class SuperadminUser(Base):
    """A platform-owner account with cross-tenant authority."""

    __tablename__ = "superadmin_users"

    id: Mapped[UuidPK]

    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    # Hashed password (PBKDF2-HMAC-SHA256, 100k iterations, per-row salt).
    # Layout: `pbkdf2_sha256$100000$<salt_b64>$<hash_b64>` so the algorithm
    # and parameters live alongside the hash — we can rotate them later
    # without losing the ability to verify existing rows.
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    # Stored as a short string ("true" / "false") so the SQLite test
    # shim does not have to learn a second boolean style. Mapped as
    # `str` to match the stored representation and the way the auth
    # service consumes the value (e.g. `user.is_active != "true"`).
    is_active: Mapped[str] = mapped_column(
        String(8), nullable=False, default="true", server_default="true"
    )

    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]

    tokens: Mapped[list["ApiToken"]] = relationship(
        back_populates="superadmin",
        cascade="all, delete-orphan",
    )


class ApiToken(Base):
    """An opaque bearer token issued for an authenticated principal.

    We store the SHA-256 hash of the token, not the token itself, so a
    database leak does not leak live credentials. The raw token is
    returned to the caller exactly once on `POST /superadmin/auth/login`.
    """

    __tablename__ = "api_tokens"

    id: Mapped[UuidPK]

    # Polymorphic principal FK: one of `superadmin_id` or
    # `tenant_user_id` is set, depending on `scope`.
    superadmin_id: Mapped[PG_UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("superadmin_users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    # Tenant user FK (set when scope is "tenant").
    tenant_user_id: Mapped[PG_UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenant_users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    # Denormalised tenant_id for fast tenant-scoped token lookup
    # (no join to tenant_users required).
    tenant_id: Mapped[PG_UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
        index=True,
    )

    # SHA-256 of the raw token, hex-encoded (64 chars).
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    # Human label, e.g. "cli", "dashboard-session-2026-06-25".
    label: Mapped[str] = mapped_column(String(120), nullable=False, default="")

    scope: Mapped[str] = mapped_column(
        String(32), nullable=False, default="superadmin", server_default="superadmin"
    )

    # Nullable so we can revoke a token without losing the row.
    revoked_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_used_at: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[CreatedAt]

    superadmin: Mapped["SuperadminUser | None"] = relationship(back_populates="tokens")
    tenant_user: Mapped["TenantUser | None"] = relationship(back_populates="tokens")

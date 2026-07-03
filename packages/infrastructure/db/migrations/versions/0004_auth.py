"""Add superadmin auth (superadmin_users + api_tokens).

Revision ID: 0004_auth
Revises: 0003_service_code
Create Date: 2026-06-25

Two global tables (no `tenant_id`):

- `superadmin_users`  — platform-owner accounts; one row per superadmin.
  Passwords are stored as `pbkdf2_sha256$<iters>$<salt>$<hash>` so the
  algorithm and parameter count live next to the digest and we can
  rotate them later without losing the ability to verify existing rows.
- `api_tokens`        — opaque bearer tokens; we store the SHA-256 of
  the token, not the token itself, so a database leak does not leak
  live credentials. Each token is bound to one superadmin and lives
  until explicitly revoked (`revoked_at` set).

No new Postgres ENUMs are required. The `is_active` flag is stored as
a short string to match the rest of the schema (so the SQLite test
shim does not have to learn about a new enum).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0004_auth"
down_revision: str | None = "0003_service_code"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "superadmin_users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        # Stored as a short string for compatibility with the rest of the
        # schema (and with the SQLite test shim). Values: "true" / "false".
        sa.Column("is_active", sa.String(8), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_superadmin_users_email", "superadmin_users", ["email"], unique=True)

    op.create_table(
        "api_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("superadmin_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("label", sa.String(120), nullable=False, server_default=""),
        sa.Column("scope", sa.String(32), nullable=False, server_default="superadmin"),
        sa.Column("revoked_at", sa.String(64), nullable=True),
        sa.Column("last_used_at", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["superadmin_id"], ["superadmin_users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_api_tokens_superadmin_id", "api_tokens", ["superadmin_id"])
    op.create_index("ix_api_tokens_token_hash", "api_tokens", ["token_hash"], unique=True)


def downgrade() -> None:
    op.drop_table("api_tokens")
    op.drop_table("superadmin_users")

"""Add tenant user auth (tenant_users + api_tokens columns).

Revision ID: 0005_tenant_auth
Revises: 0004_auth
Create Date: 2026-06-26

Adds the `tenant_users` table — one row per per-tenant user account —
and extends `api_tokens` with polymorphic principal columns so a single
token can reference either a superadmin or a tenant user.

The migration makes `superadmin_id` nullable in `api_tokens` and adds
`tenant_user_id` (nullable FK to tenant_users) plus a denormalised
`tenant_id` column for fast tenant-scoped lookups.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0005_tenant_auth"
down_revision: str | None = "0004_auth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- tenant_users table ------------------------------------------------
    op.create_table(
        "tenant_users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column(
            "is_active", sa.String(8), nullable=False, server_default="true"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_tenant_users_tenant_id", "tenant_users", ["tenant_id"]
    )
    op.create_index(
        "ix_tenant_users_email", "tenant_users", ["email"], unique=True
    )

    # --- api_tokens: add polymorphic principal columns --------------------
    # Make superadmin_id nullable (existing rows keep their value).
    op.alter_column(
        "api_tokens",
        "superadmin_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )

    # Add tenant_user_id FK (nullable — only set for tenant-scoped tokens).
    op.add_column(
        "api_tokens",
        sa.Column(
            "tenant_user_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_api_tokens_tenant_user_id",
        "api_tokens",
        ["tenant_user_id"],
    )
    op.create_foreign_key(
        "fk_api_tokens_tenant_user_id_tenant_users",
        "api_tokens",
        "tenant_users",
        ["tenant_user_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # Add denormalised tenant_id for fast lookup.
    op.add_column(
        "api_tokens",
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_api_tokens_tenant_id",
        "api_tokens",
        ["tenant_id"],
    )


def downgrade() -> None:
    # Remove tenant columns from api_tokens.
    op.drop_index("ix_api_tokens_tenant_id", table_name="api_tokens")
    op.drop_column("api_tokens", "tenant_id")

    op.drop_constraint(
        "fk_api_tokens_tenant_user_id_tenant_users",
        "api_tokens",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_api_tokens_tenant_user_id", table_name="api_tokens"
    )
    op.drop_column("api_tokens", "tenant_user_id")

    # Restore superadmin_id to NOT NULL.
    op.alter_column(
        "api_tokens",
        "superadmin_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )

    # Drop tenant_users table.
    op.drop_table("tenant_users")

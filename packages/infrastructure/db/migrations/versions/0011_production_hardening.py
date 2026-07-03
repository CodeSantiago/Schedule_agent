"""Add idempotency_keys and conversation_locks tables for production hardening.

Revision ID: 0011_production_hardening
Revises: 0010_fix_sqlite_schema_drift
Create Date: 2026-06-30

Adds two new tables:

1. **idempotency_keys** — content-based dedup with a TTL window so that
   rapid retries or burst deliveries do not produce duplicate work.
   Uses ``(tenant_id, idempotency_key)`` as the unique constraint where
   the key is a SHA-256 hash of ``(customer_phone, body_bytes)``.

2. **conversation_locks** — per-customer advisory lock that serializes
   message processing within a configurable TTL so multiple workers
   cannot interleave two messages from the same customer.

Both tables use an ``expires_at`` column for TTL semantics. Old rows
can be pruned periodically by a background sweep (not part of this
migration; the application code ignores expired rows at read time).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011_production_hardening"
down_revision: str | None = "0010_fix_sqlite_schema_drift"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── idempotency_keys ──────────────────────────────────────────────────
    op.create_table(
        "idempotency_keys",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("customer_phone", sa.String(32), nullable=False),
        sa.Column("body_hash", sa.String(64), nullable=False),
        sa.Column(
            "provider_message_id", sa.String(128), nullable=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_idempotency_key_per_tenant",
        ),
    )
    op.create_index(
        "ix_idempotency_keys_expires_at",
        "idempotency_keys",
        ["expires_at"],
    )
    op.create_index(
        "ix_idempotency_keys_tenant_phone",
        "idempotency_keys",
        ["tenant_id", "customer_phone"],
    )

    # ── conversation_locks ────────────────────────────────────────────────
    op.create_table(
        "conversation_locks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("customer_phone", sa.String(32), nullable=False),
        sa.Column("worker_id", sa.String(64), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")
        ),
        sa.Column(
            "acquired_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "customer_phone",
            name="uq_conversation_lock_per_customer",
        ),
    )
    op.create_index(
        "ix_conversation_locks_expires_at",
        "conversation_locks",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_table("conversation_locks")
    op.drop_table("idempotency_keys")

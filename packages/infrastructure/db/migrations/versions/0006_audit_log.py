"""Add tenant-scoped audit / operational log table.

Revision ID: 0006_audit_log
Revises: 0005_tenant_auth
Create Date: 2026-06-26

Adds the ``tenant_audit_logs`` table — one row per auditable event or
operational log entry. Shared between audit trail (settings changes
with actor identity) and operational events (bot paused, closed-date
rejection, etc.).

See ``packages.infrastructure.db.models.audit_log.TenantAuditLog`` for
the column documentation.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0006_audit_log"
down_revision: str | None = "0005_tenant_auth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    json_type = sa.JSON() if conn.dialect.name == "sqlite" else postgresql.JSONB()

    op.create_table(
        "tenant_audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("event_type", sa.String(64), nullable=False, index=True),
        sa.Column("level", sa.String(16), nullable=False, server_default="info"),
        sa.Column("message", sa.Text(), nullable=False, server_default=""),
        sa.Column("actor_scope", sa.String(32), nullable=True, index=True),
        sa.Column("actor_id", sa.String(64), nullable=True),
        sa.Column("changed_fields", json_type, nullable=True),
        sa.Column("details", json_type, nullable=False, server_default="{}"),
        sa.Column("duration_ms", sa.Integer, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_tenant_audit_logs_tenant_created",
        "tenant_audit_logs",
        ["tenant_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_tenant_audit_logs_tenant_created", table_name="tenant_audit_logs")
    op.drop_table("tenant_audit_logs")

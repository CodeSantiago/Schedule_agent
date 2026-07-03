"""Add role column to tenant_users for per-tenant RBAC.

Revision ID: 0007_tenant_roles
Revises: 0006_audit_log
Create Date: 2026-06-29

Adds a ``role`` column to the ``tenant_users`` table with a server
default of ``"admin"`` so existing rows become admin by default.
Also adds the ``tenant_users.name`` index for faster lookups.

Roles:
- ``owner``  — full access (bot toggle, data settings, Sheets,
  imports, destructive ops, user management).
- ``admin``  — nearly everything except destructive/critical settings
  and user management.
- ``staff``  — appointment management + read-only on everything else.
- ``viewer`` — read-only access (dashboard, appointments list).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007_tenant_roles"
down_revision: str | None = "0006_audit_log"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tenant_users",
        sa.Column(
            "role",
            sa.String(32),
            nullable=False,
            server_default="admin",
        ),
    )
    op.create_index(
        "ix_tenant_users_role", "tenant_users", ["role"]
    )


def downgrade() -> None:
    op.drop_index("ix_tenant_users_role", table_name="tenant_users")
    op.drop_column("tenant_users", "role")

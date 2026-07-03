"""Add customer identity fields to appointments + barber_id to tenant_users.

Revision ID: 0008_identity_barber_link
Revises: 0007_tenant_roles
Create Date: 2026-06-29

Adds:
- ``customer_dni`` (nullable VARCHAR 32) and ``customer_last_name``
  (nullable VARCHAR 120) to the ``appointments`` table for configurable
  customer identity capture.
- ``barber_id`` (nullable UUID FK → barbers.id) to ``tenant_users`` so
  a tenant user can be linked to a specific barber for auto-landing on
  the barber workspace.

These columns are additive: existing rows get NULL, existing code that
only reads ``customer_name`` keeps working unchanged.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008_identity_barber_link"
down_revision: str | None = "0007_tenant_roles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- Appointments: customer identity fields ---
    op.add_column(
        "appointments",
        sa.Column("customer_dni", sa.String(32), nullable=True),
    )
    op.add_column(
        "appointments",
        sa.Column("customer_last_name", sa.String(120), nullable=True),
    )

    # --- Tenant users: optional barber link ---
    op.add_column(
        "tenant_users",
        sa.Column("barber_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_tenant_users_barber_id_barbers",
        "tenant_users",
        "barbers",
        ["barber_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_tenant_users_barber_id", "tenant_users", ["barber_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_tenant_users_barber_id", table_name="tenant_users")
    op.drop_constraint(
        "fk_tenant_users_barber_id_barbers", "tenant_users", type_="foreignkey"
    )
    op.drop_column("tenant_users", "barber_id")
    op.drop_column("appointments", "customer_last_name")
    op.drop_column("appointments", "customer_dni")

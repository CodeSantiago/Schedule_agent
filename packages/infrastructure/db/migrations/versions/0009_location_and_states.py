"""Add ``location`` column to tenants + new session funnel states.

Revision ID: 0009_location_and_states
Revises: 0008_identity_barber_link
Create Date: 2026-06-29

Adds:
- ``location`` (nullable VARCHAR 200) to the ``tenants`` table for
  the bot's greeting / LLM prompt context.
- New session state enum values for the richer conversational funnel:
  ``confirmacion_turno``, ``turno_cancelado``,
  ``seleccion_nuevo_horario``, ``turno_reagendado``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009_location_and_states"
down_revision: str | None = "0008_identity_barber_link"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# New session states added to the ``session_state`` enum.
_NEW_STATES = (
    "confirmacion_turno",
    "turno_cancelado",
    "seleccion_nuevo_horario",
    "turno_reagendado",
)


def upgrade() -> None:
    # ── Tenants: location column ──────────────────────────────────────
    op.add_column(
        "tenants",
        sa.Column("location", sa.String(200), nullable=True),
    )

    # ── Session state enum (Postgres only) ───────────────────────────
    # We run ``ALTER TYPE … ADD VALUE`` for each new state. This cannot
    # be done inside a transaction block; Alembic may handle this
    # automatically depending on the dialect.
    conn = op.get_bind()
    if conn.dialect.name == "postgresql":
        # Check which values already exist (idempotent).
        existing = _existing_enum_values(conn, "session_state")
        for val in _NEW_STATES:
            if val not in existing:
                op.execute(
                    sa.text(
                        f"ALTER TYPE session_state ADD VALUE '{val}'"
                    )
                )


def downgrade() -> None:
    # ── Tenants: remove location column ───────────────────────────────
    op.drop_column("tenants", "location")

    # ── Session state enum ────────────────────────────────────────────
    # Postgres does not support removing values from an enum.
    # For the downgrade we document the limitation and do nothing.
    # If you need to revert, create a new type and migrate columns.
    pass


def _existing_enum_values(conn, enum_name: str) -> set[str]:
    """Query the existing values of a Postgres enum type."""
    row = conn.execute(
        sa.text(
            "SELECT array_agg(v.enumlabel::text) AS vals "
            "FROM pg_enum v "
            "JOIN pg_type t ON v.enumtypid = t.oid "
            "WHERE t.typname = :name"
        ),
        {"name": enum_name},
    ).scalar()
    return set(row) if row else set()

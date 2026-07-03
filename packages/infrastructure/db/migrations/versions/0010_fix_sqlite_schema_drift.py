"""Fix SQLite schema drift: session_state CHECK constraint + tenants.location.

Revision ID: 0010_fix_sqlite_schema_drift
Revises: 0009_location_and_states
Create Date: 2026-06-29

Fixes two issues that affect SQLite databases (dev/test):

1. **session_state CHECK constraint drift**. Migration 0009 added
   ``confirmacion_turno``, ``turno_cancelado``, ``seleccion_nuevo_horario``,
   and ``turno_reagendado`` to the PostgreSQL enum via ``ALTER TYPE … ADD VALUE``,
   but SQLite CHECK constraints are immutable after table creation — the old
   constraint still only has the original 0001-initial values. Attempting to
   persist any new state raises::

       sqlite3.IntegrityError: CHECK constraint failed: ck_conversation_sessions_session_state

   Alembic batch operations recreate the table with the updated constraint set
   drawn from ``SESSION_STATE_VALUES`` (the single source of truth).

2. **tenants.location column missing**. ``op.add_column`` from 0009 works for
   both dialects, but if a SQLite database was created or migrated outside the
   standard chain, the column can still be absent. We add it idempotently here
   as a safety net.

PostgreSQL is unaffected (the enum was already updated by 0009, and
``tenants.location`` from 0009 ``op.add_column`` already exists).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = "0010_fix_sqlite_schema_drift"
down_revision: str | None = "0009_location_and_states"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. Ensure tenants.location exists (idempotent, both dialects) ─────
    _ensure_column(conn, "tenants", "location", sa.String(200), nullable=True)

    # ── 2. Fix SQLite session_state CHECK constraint ──────────────────────
    if conn.dialect.name == "sqlite":
        _fix_sqlite_session_state_constraint()


def downgrade() -> None:
    """Downgrade is a no-op.

    We cannot safely revert the CHECK constraint to an older value set
    (SQLite would need another table recreation), and removing
    ``tenants.location`` would lose data. The forward migration is the
    safe direction only.
    """
    pass


# ── Helpers ──────────────────────────────────────────────────────────────────


def _ensure_column(
    conn,
    table: str,
    column: str,
    type_,
    **kwargs,
) -> None:
    """Add *column* to *table* only if it does not already exist."""
    inspector = inspect(conn)
    existing = {c["name"] for c in inspector.get_columns(table)}
    if column not in existing:
        op.add_column(table, sa.Column(column, type_, **kwargs))


def _fix_sqlite_session_state_constraint() -> None:
    """Recreate the ``session_state`` CHECK constraint with ALL current values.

    SQLite CHECK constraints are part of the ``CREATE TABLE`` statement and
    cannot be modified after the fact. Alembic's batch mode handles this by
    creating a temporary table with the new schema, copying data, and swapping.

    We load ``SESSION_STATE_VALUES`` directly from the model as the single
    source of truth — this ensures the constraint is always in sync.
    """
    from packages.infrastructure.db.models.messaging import SESSION_STATE_VALUES

    conn = op.get_bind()
    inspector = inspect(conn)

    # Reflect existing CHECK constraints so we can drop them all.
    existing_checks = inspector.get_check_constraints("conversation_sessions")

    values_sql = ", ".join(f"'{v}'" for v in SESSION_STATE_VALUES)
    check_condition = f"state IN ({values_sql})"

    with op.batch_alter_table("conversation_sessions", recreate="always") as batch_op:
        # Change column to plain VARCHAR(32) — this strips any old type-level
        # CHECK constraint the reflected schema might carry.
        batch_op.alter_column(
            "state",
            type_=sa.String(32),
            nullable=False,
            server_default="inicio",
        )
        # Drop ALL existing CHECK constraints on this table. For migration-
        # created databases this is the ``'session_state'`` constraint from
        # 0001's ``postgresql.ENUM(name="session_state")``. For
        # ``create_all()``-created databases this will be
        # ``'ck_conversation_sessions_session_state'``.
        for c in existing_checks:
            name = c.get("name")
            if name:
                batch_op.drop_constraint(name, type_="check")
        # Create the definitive CHECK constraint with *all* current state values.
        batch_op.create_check_constraint(
            "ck_conversation_sessions_session_state",
            check_condition,
        )

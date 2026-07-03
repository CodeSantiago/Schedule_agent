"""Migrate session state values from Spanish to English.

SessionStateEnum had Spanish values (``inicio``, ``esperando_menu``, …).
This migration replaces them with English values (``start``, ``awaiting_menu``, …).

Strategy
--------
- **Postgres**: Create a new ``session_state_new`` enum type with the English
  values, ALTER the column to use the new type (with a USING cast via text),
  then drop the old type.
- **SQLite**: Since SQLite uses CHECK constraints for enum emulation, we
  drop and recreate the CHECK constraint with the new values.

Existing rows with Spanish state values are migrated to English via a
simple UPDATE before the constraint is swapped.  Idempotent: rows that
already have English values are left unchanged.

Revision ID: 0013_session_state_english
Revises: 0012_tenant_user_photo
Create Date: 2026-07-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0013_session_state_english"
down_revision: str | None = "0012_tenant_user_photo"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Mapping of old Spanish → new English state values.
_SPANISH_TO_ENGLISH: dict[str, str] = {
    "inicio": "start",
    "esperando_menu": "awaiting_menu",
    "esperando_servicio": "awaiting_service",
    "esperando_dia": "awaiting_day",
    "esperando_barbero": "awaiting_barber",
    "esperando_horario": "awaiting_time",
    "esperando_nombre": "awaiting_name",
    "confirmacion_turno": "booking_confirmation",
    "turno_confirmado": "booking_confirmed",
    "esperando_cancelacion": "awaiting_cancellation",
    "turno_cancelado": "booking_cancelled",
    "esperando_reagendar": "awaiting_reschedule",
    "seleccion_turno_cancelar": "selecting_cancel_appointment",
    "seleccion_turno_reagendar": "selecting_reschedule_appointment",
    "seleccion_nuevo_horario": "selecting_new_time",
    "turno_reagendado": "booking_rescheduled",
    "idle": "idle",
    "closed": "closed",
}

# New English state values in order.
_ENGLISH_STATES = tuple(_SPANISH_TO_ENGLISH.values())


def upgrade() -> None:
    conn = op.get_bind()
    dialect = conn.dialect.name

    if dialect == "postgresql":
        # Postgres: UPDATE first (old enum still accepts old values),
        # then replace the enum type with English.
        _migrate_rows()
        _default_unknown_rows()
        _upgrade_postgres(conn)
    else:
        # SQLite: relax the CHECK constraint first (drop old, add new),
        # then update values.
        _upgrade_sqlite(conn)
        _migrate_rows()
        _default_unknown_rows()


def _migrate_rows() -> None:
    """Update all existing rows from Spanish to English state values."""
    for old_val, new_val in _SPANISH_TO_ENGLISH.items():
        if old_val == new_val:
            continue
        op.execute(
            sa.text(
                "UPDATE conversation_sessions "
                "SET state = :new_val "
                "WHERE state = :old_val"
            ).bindparams(old_val=old_val, new_val=new_val)
        )


def _default_unknown_rows() -> None:
    """Set any remaining unknown state to 'start'."""
    # SQLite does not support bound tuples in IN clauses, so we construct
    # the value list as literal SQL from the hardcoded constant.
    values_sql = ", ".join(f"'{v}'" for v in _ENGLISH_STATES)
    op.execute(
        sa.text(
            f"UPDATE conversation_sessions "
            f"SET state = 'start' "
            f"WHERE state NOT IN ({values_sql})"
        )
    )


def downgrade() -> None:
    """Reverse the migration (English → Spanish).

    This is destructive: English state values that have no Spanish
    equivalent are mapped to their closest Spanish counterpart.
    """
    conn = op.get_bind()
    dialect = conn.dialect.name

    # Reverse mapping.
    _english_to_spanish = {v: k for k, v in _SPANISH_TO_ENGLISH.items()}

    for eng_val, spa_val in _english_to_spanish.items():
        if eng_val == spa_val:
            continue
        op.execute(
            sa.text(
                "UPDATE conversation_sessions "
                "SET state = :spa_val "
                "WHERE state = :eng_val"
            ).bindparams(eng_val=eng_val, spa_val=spa_val)
        )

    if dialect == "postgresql":
        _downgrade_postgres(conn)
    else:
        _downgrade_sqlite(conn)


# ── Postgres helpers ───────────────────────────────────────────────────────


def _upgrade_postgres(conn) -> None:
    """Replace the Postgres enum type with English values."""
    # Create the new enum type.
    op.execute("CREATE TYPE session_state_new AS ENUM ('" + "', '".join(_ENGLISH_STATES) + "')")

    # ALTER the column to use the new type, casting through text.
    op.execute(
        "ALTER TABLE conversation_sessions "
        "ALTER COLUMN state TYPE session_state_new "
        "USING state::text::session_state_new"
    )

    # Drop the old enum type.
    op.execute("DROP TYPE session_state")

    # Rename the new type to the original name.
    op.execute("ALTER TYPE session_state_new RENAME TO session_state")


def _downgrade_postgres(conn) -> None:
    """Restore the Postgres enum type with Spanish values."""
    spanish_states = tuple(_SPANISH_TO_ENGLISH.keys())
    op.execute("CREATE TYPE session_state_old AS ENUM ('" + "', '".join(spanish_states) + "')")

    op.execute(
        "ALTER TABLE conversation_sessions "
        "ALTER COLUMN state TYPE session_state_old "
        "USING state::text::session_state_old"
    )
    op.execute("DROP TYPE session_state")
    op.execute("ALTER TYPE session_state_old RENAME TO session_state")


# ── SQLite helpers ─────────────────────────────────────────────────────────


def _get_sqlite_check_constraints(conn, table: str) -> list[str]:
    """Return the SQL text of CHECK constraints on a table."""
    from sqlalchemy import inspect

    inspector = inspect(conn)
    constraints = []
    for constraint in inspector.get_check_constraints(table):
        sql = constraint.get("sqltext", "")
        if sql:
            constraints.append(sql)
    return constraints


def _upgrade_sqlite(conn) -> None:
    """Replace the CHECK constraint with English values via table rebuild.

    SQLite cannot ALTER constraints, so we:
    1. Create a temp table with NO CHECK constraint.
    2. Copy all data verbatim.
    3. Drop the old table.
    4. Re-create it with the new CHECK constraint.
    5. Copy data back.
    6. Drop the temp table.
    """
    # Step 0: Clean up any leftover temp table from a prior failed attempt.
    op.execute("DROP TABLE IF EXISTS conversation_sessions_tmp")

    # Step 1: Create temp table without constraint.
    _CREATE_TEMP = """
        CREATE TABLE conversation_sessions_tmp (
            id CHAR(32) NOT NULL,
            tenant_id CHAR(32) NOT NULL,
            channel VARCHAR(32) NOT NULL DEFAULT 'whatsapp',
            customer_phone VARCHAR(32) NOT NULL,
            state VARCHAR(32) NOT NULL DEFAULT 'start',
            context JSON NOT NULL DEFAULT '{}',
            last_message_seq INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            UNIQUE (tenant_id, channel, customer_phone),
            FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
        )
    """
    op.execute(_CREATE_TEMP)

    # Step 2: Copy data.
    op.execute("INSERT INTO conversation_sessions_tmp SELECT * FROM conversation_sessions")

    # Step 2b: Migrate state values to English in the temp table.
    for old_val, new_val in _SPANISH_TO_ENGLISH.items():
        if old_val == new_val:
            continue
        op.execute(
            sa.text(
                "UPDATE conversation_sessions_tmp "
                "SET state = :new_val "
                "WHERE state = :old_val"
            ).bindparams(old_val=old_val, new_val=new_val)
        )

    # Step 3: Drop old table.
    op.execute("DROP TABLE conversation_sessions")

    # Step 4: Create new table with English CHECK constraint.
    values_sql = ", ".join(f"'{v}'" for v in _ENGLISH_STATES)
    _CREATE_NEW = f"""
        CREATE TABLE conversation_sessions (
            id CHAR(32) NOT NULL,
            tenant_id CHAR(32) NOT NULL,
            channel VARCHAR(32) NOT NULL DEFAULT 'whatsapp',
            customer_phone VARCHAR(32) NOT NULL,
            state VARCHAR(32) NOT NULL DEFAULT 'start',
            context JSON NOT NULL DEFAULT '{{}}',
            last_message_seq INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            UNIQUE (tenant_id, channel, customer_phone),
            FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
            CONSTRAINT ck_conversation_sessions_session_state CHECK (state IN ({values_sql}))
        )
    """
    op.execute(_CREATE_NEW)

    # Step 5: Copy data back.
    op.execute("INSERT INTO conversation_sessions SELECT * FROM conversation_sessions_tmp")

    # Step 6: Clean up temp table.
    op.execute("DROP TABLE conversation_sessions_tmp")


def _downgrade_sqlite(conn) -> None:
    """Restore the CHECK constraint with Spanish values via table rebuild."""

    op.execute("DROP TABLE IF EXISTS conversation_sessions_tmp")

    # Same pattern: temp table, copy, drop, create with old CHECK, copy back.
    _CREATE_TEMP = """
        CREATE TABLE conversation_sessions_tmp (
            id CHAR(32) NOT NULL,
            tenant_id CHAR(32) NOT NULL,
            channel VARCHAR(32) NOT NULL DEFAULT 'whatsapp',
            customer_phone VARCHAR(32) NOT NULL,
            state VARCHAR(32) NOT NULL DEFAULT 'inicio',
            context JSON NOT NULL DEFAULT '{}',
            last_message_seq INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            UNIQUE (tenant_id, channel, customer_phone),
            FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
        )
    """
    op.execute(_CREATE_TEMP)
    op.execute("INSERT INTO conversation_sessions_tmp SELECT * FROM conversation_sessions")
    op.execute("DROP TABLE conversation_sessions")

    spanish_states = tuple(_SPANISH_TO_ENGLISH.keys())
    values_sql = ", ".join(f"'{v}'" for v in spanish_states)
    _CREATE_NEW = f"""
        CREATE TABLE conversation_sessions (
            id CHAR(32) NOT NULL,
            tenant_id CHAR(32) NOT NULL,
            channel VARCHAR(32) NOT NULL DEFAULT 'whatsapp',
            customer_phone VARCHAR(32) NOT NULL,
            state VARCHAR(32) NOT NULL DEFAULT 'inicio',
            context JSON NOT NULL DEFAULT '{{}}',
            last_message_seq INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            UNIQUE (tenant_id, channel, customer_phone),
            FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
            CONSTRAINT ck_conversation_sessions_session_state CHECK (state IN ({values_sql}))
        )
    """
    op.execute(_CREATE_NEW)
    op.execute("INSERT INTO conversation_sessions SELECT * FROM conversation_sessions_tmp")
    op.execute("DROP TABLE conversation_sessions_tmp")

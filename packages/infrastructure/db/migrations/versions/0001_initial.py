"""${message}

Revision ID: ${up_revision}
Revises:
Create Date: ${create_date}
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- Enums ---------------------------------------------------------
    tenant_status = postgresql.ENUM(
        "active", "suspended", "trial", "churned",
        name="tenant_status", create_type=False,
    )
    tenant_status.create(op.get_bind(), checkfirst=True)

    provider_kind = postgresql.ENUM(
        "whatsapp", "llm", "calendar", "sheets", "sms",
        name="provider_kind", create_type=False,
    )
    provider_kind.create(op.get_bind(), checkfirst=True)

    weekday = postgresql.ENUM(
        "mon", "tue", "wed", "thu", "fri", "sat", "sun",
        name="weekday", create_type=False,
    )
    weekday.create(op.get_bind(), checkfirst=True)

    appointment_status = postgresql.ENUM(
        "pending", "confirmed", "cancelled", "completed", "no_show",
        name="appointment_status", create_type=False,
    )
    appointment_status.create(op.get_bind(), checkfirst=True)

    session_state = postgresql.ENUM(
        "inicio", "esperando_menu", "esperando_servicio", "esperando_dia",
        "esperando_barbero", "esperando_horario", "esperando_nombre",
        "turno_confirmado", "esperando_cancelacion", "esperando_reagendar",
        "seleccion_turno_cancelar", "seleccion_turno_reagendar",
        "idle", "closed",
        name="session_state", create_type=False,
    )
    session_state.create(op.get_bind(), checkfirst=True)

    message_status = postgresql.ENUM(
        "received", "processing", "processed", "failed", "sent", "delivered",
        name="message_status", create_type=False,
    )
    message_status.create(op.get_bind(), checkfirst=True)

    # --- Tables --------------------------------------------------------
    op.create_table(
        "tenants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False, unique=True),
        sa.Column("status", tenant_status, nullable=False, server_default="trial"),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "tenant_settings",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("config", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
    )

    op.create_table(
        "provider_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", provider_kind, nullable=False),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("provider_name", sa.String(64), nullable=False),
        sa.Column("credentials", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("settings", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_provider_configs_tenant_id", "provider_configs", ["tenant_id"])
    op.create_index("ix_provider_configs_kind", "provider_configs", ["kind"])
    # Partial unique index: at most one ACTIVE config per (tenant, kind).
    # The original migration carried a full unique constraint
    # `uq_provider_active_per_kind` that blocked two inactive rows for the
    # same kind; replacing it with a partial unique index is the standard
    # Postgres pattern for "at most one active per kind" and matches the
    # documented intent in the model + README.
    op.create_index(
        "uq_provider_active_per_kind",
        "provider_configs",
        ["tenant_id", "kind"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )

    op.create_table(
        "barbers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("restrictions", sa.String(64), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_barbers_tenant_id", "barbers", ["tenant_id"])

    op.create_table(
        "services",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("duration_minutes", sa.Integer, nullable=False),
        sa.Column("price_cents", sa.Integer, nullable=False, server_default="0"),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_services_tenant_id", "services", ["tenant_id"])

    op.create_table(
        "barber_schedules",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("barber_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("weekday", weekday, nullable=False),
        sa.Column("start_time", sa.Time, nullable=False),
        sa.Column("end_time", sa.Time, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["barber_id"], ["barbers.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("barber_id", "weekday", "start_time", name="uq_barber_schedule_slot"),
    )
    op.create_index("ix_barber_schedules_barber_id", "barber_schedules", ["barber_id"])

    op.create_table(
        "appointments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("barber_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("service_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("appointment_date", sa.Date, nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", appointment_status, nullable=False, server_default="pending"),
        sa.Column("customer_name", sa.String(120), nullable=False),
        sa.Column("customer_phone", sa.String(32), nullable=False),
        sa.Column("notes", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["barber_id"], ["barbers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["service_id"], ["services.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "tenant_id", "barber_id", "appointment_date", "start_time",
            name="uq_appointment_slot",
        ),
    )
    op.create_index("ix_appointments_tenant_id", "appointments", ["tenant_id"])
    op.create_index("ix_appointments_barber_id", "appointments", ["barber_id"])
    op.create_index("ix_appointments_service_id", "appointments", ["service_id"])
    op.create_index("ix_appointments_appointment_date", "appointments", ["appointment_date"])
    op.create_index("ix_appointments_customer_phone", "appointments", ["customer_phone"])

    op.create_table(
        "conversation_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel", sa.String(32), nullable=False, server_default="whatsapp"),
        sa.Column("customer_phone", sa.String(32), nullable=False),
        sa.Column("state", session_state, nullable=False, server_default="inicio"),
        sa.Column("context", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("last_message_seq", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id", "channel", "customer_phone", name="uq_session_per_customer"),
    )
    op.create_index("ix_conversation_sessions_tenant_id", "conversation_sessions", ["tenant_id"])
    op.create_index("ix_conversation_sessions_customer_phone", "conversation_sessions", ["customer_phone"])

    op.create_table(
        "incoming_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider_message_id", sa.String(128), nullable=False),
        sa.Column("channel", sa.String(32), nullable=False, server_default="whatsapp"),
        sa.Column("from_phone", sa.String(32), nullable=False),
        sa.Column("body", sa.Text, nullable=False, server_default=""),
        sa.Column("raw_payload", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", message_status, nullable=False, server_default="received"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["conversation_sessions.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id", "provider_message_id", name="uq_incoming_provider_message"),
    )
    op.create_index("ix_incoming_messages_tenant_id", "incoming_messages", ["tenant_id"])
    op.create_index("ix_incoming_messages_session_id", "incoming_messages", ["session_id"])
    op.create_index("ix_incoming_messages_from_phone", "incoming_messages", ["from_phone"])

    op.create_table(
        "outgoing_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("to_phone", sa.String(32), nullable=False),
        sa.Column("body", sa.Text, nullable=False, server_default=""),
        sa.Column("provider_message_id", sa.String(128), nullable=True),
        sa.Column("status", message_status, nullable=False, server_default="sent"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["conversation_sessions.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_outgoing_messages_tenant_id", "outgoing_messages", ["tenant_id"])
    op.create_index("ix_outgoing_messages_session_id", "outgoing_messages", ["session_id"])
    op.create_index("ix_outgoing_messages_to_phone", "outgoing_messages", ["to_phone"])


def downgrade() -> None:
    # Drop tables in reverse FK order, then enum types.
    op.drop_table("outgoing_messages")
    op.drop_table("incoming_messages")
    op.drop_table("conversation_sessions")
    op.drop_table("appointments")
    op.drop_table("barber_schedules")
    op.drop_table("services")
    op.drop_table("barbers")
    op.drop_table("provider_configs")
    op.drop_table("tenant_settings")
    op.drop_table("tenants")

    bind = op.get_bind()
    postgresql.ENUM(name="message_status").drop(bind, checkfirst=True)
    postgresql.ENUM(name="session_state").drop(bind, checkfirst=True)
    postgresql.ENUM(name="appointment_status").drop(bind, checkfirst=True)
    postgresql.ENUM(name="weekday").drop(bind, checkfirst=True)
    postgresql.ENUM(name="provider_kind").drop(bind, checkfirst=True)
    postgresql.ENUM(name="tenant_status").drop(bind, checkfirst=True)

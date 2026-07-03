"""Add date-specific barber overrides (absences + extra hours).

Revision ID: 0002_part2_overrides
Revises: 0001_initial
Create Date: 2026-06-25

Adds two tenant-aware tables that capture the operational control
tenant operators need on top of the weekly recurring schedule:

- `barber_absences`   — barber unavailable on a given date (whole day or a
  partial time range). Tenants create these when someone is sick, on
  vacation, or running a personal errand.
- `barber_extra_hours` — extra availability outside the weekly schedule
  (e.g. picking up a Saturday shift). Multiple rows per barber/date are
  allowed and are merged with the weekly schedule by the domain layer.

No new Postgres ENUMs are required. The existing `weekday` enum from
0001_initial is not used in these tables (we use a real `Date` instead).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002_part2_overrides"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "barber_absences",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("barber_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("absence_date", sa.Date, nullable=False),
        sa.Column("start_time", sa.Time, nullable=True),
        sa.Column("end_time", sa.Time, nullable=True),
        sa.Column("reason", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["barber_id"], ["barbers.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_barber_absences_barber_id", "barber_absences", ["barber_id"])
    op.create_index("ix_barber_absences_absence_date", "barber_absences", ["absence_date"])

    op.create_table(
        "barber_extra_hours",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("barber_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("extra_date", sa.Date, nullable=False),
        sa.Column("start_time", sa.Time, nullable=False),
        sa.Column("end_time", sa.Time, nullable=False),
        sa.Column("reason", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["barber_id"], ["barbers.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_barber_extra_hours_barber_id", "barber_extra_hours", ["barber_id"])
    op.create_index("ix_barber_extra_hours_extra_date", "barber_extra_hours", ["extra_date"])


def downgrade() -> None:
    op.drop_table("barber_extra_hours")
    op.drop_table("barber_absences")

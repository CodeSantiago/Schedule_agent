"""Add `services.code` short code (C / B / CB / OTHER).

Revision ID: 0003_service_code
Revises: 0002_part2_overrides
Create Date: 2026-06-25

The legacy bot used short codes ("C", "B", "CB") to identify services
in its session state and to enforce rules like SOLO_CORTE (only "C"
allowed at restricted slots) and the 2-slot CB invariant. To preserve
those semantics in the new system without free-form string parsing, we
add an explicit `code` column on `services` and backfill existing rows
with the most common heuristic (60+ minutes → "CB", else "OTHER").

Existing rows that cannot be classified are still queryable; the new
column is non-null with a default of "OTHER" so old data is always
present.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_service_code"
down_revision: str | None = "0002_part2_overrides"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "services",
        sa.Column(
            "code",
            sa.String(8),
            nullable=False,
            server_default="OTHER",
        ),
    )
    # Backfill the obvious case: long services are CB. Anything else
    # stays as "OTHER" and a tenant operator can reclassify later.
    op.execute(
        "UPDATE services SET code = 'CB' WHERE duration_minutes >= 60"
    )


def downgrade() -> None:
    op.drop_column("services", "code")

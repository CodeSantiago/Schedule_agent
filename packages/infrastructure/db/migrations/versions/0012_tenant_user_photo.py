"""Add optional photo_url to tenant_users.

Revision ID: 0012_tenant_user_photo
Revises: 0011_production_hardening
Create Date: 2026-06-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_tenant_user_photo"
down_revision: str | None = "0011_production_hardening"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tenant_users",
        sa.Column("photo_url", sa.String(length=500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenant_users", "photo_url")

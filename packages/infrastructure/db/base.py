"""Reusable multi-tenant base for SQLAlchemy ORM models.

This module defines two distinct column annotations:

- `UuidPK`  — UUID v4, used for a table's **primary key** (always `id`).
- `UuidFK`  — UUID column used as a foreign key (NOT a primary key).

The split exists because, with `Mapped[UuidPK]` and a single shared
`MappedColumn(primary_key=True)`, SQLAlchemy 2.0 silently marks EVERY
column annotated with that alias as part of the primary key — turning
`id` + `tenant_id` into a composite PK. That was a real bug we hit in
Part 2: tenant-scoped tables rendered as composite PKs even though the
hand-written migration only had `id` as the PK. `UuidFK` is the typed
column for non-PK UUID references.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID, uuid4

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, MappedColumn

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


# --- Reusable column annotations -------------------------------------------

# Primary keys: UUID v4, server-default via `uuid4`.
UuidPK = Annotated[
    UUID,
    MappedColumn(PG_UUID(as_uuid=True), primary_key=True, default=uuid4),
]

# Foreign keys: same UUID type, but explicitly NOT a primary key.
# `index=True` because FK columns are almost always queried by.
UuidFK = Annotated[
    UUID,
    MappedColumn(PG_UUID(as_uuid=True), index=True),
]

# `created_at` / `updated_at` columns with sane server defaults.
CreatedAt = Annotated[
    datetime,
    MappedColumn(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    ),
]
UpdatedAt = Annotated[
    datetime,
    MappedColumn(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    ),
]

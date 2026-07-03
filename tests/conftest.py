"""Pytest fixtures: in-memory sqlite engine for integration tests.

The platform targets Postgres (JSONB + native ENUM types), but a
SQLite-in-memory engine with two small shims is enough for service-level
integration tests on the repos and the booking service:

1. `JSONB` columns are compiled to `JSON` (TEXT under the hood) for sqlite.
2. Native Postgres `ENUM` columns are compiled to `VARCHAR` (the SQLAlchemy
   ENUM is already a string at the Python level; the enum constraint is a
   Postgres feature only).

Production behaviour, the unique constraints, the FK cascades, the
Corte+Barba 2-slot rule, and the tenant filtering are all covered by
these tests. The Postgres-specific surface (enum values enforced at the
DB level, JSONB GIN indexes) is not — that's the cost of running
without a Postgres container in this slice.
"""

from __future__ import annotations

from typing import Iterator
from uuid import UUID, uuid4

import pytest
from sqlalchemy import JSON, create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import Session, sessionmaker


@pytest.fixture(scope="session", autouse=True)
def _install_sqlite_shims() -> None:
    """Monkey-patch the sqlite dialect to accept JSONB and Postgres ENUMs.

    Runs once per test session. Mutates the global dialect; safe because
    pytest does not run parallel tests in this project.
    """
    original_process = SQLiteTypeCompiler.process

    def process(self, type_, **kw):  # type: ignore[no-untyped-def]
        if isinstance(type_, JSONB):
            return original_process(self, JSON(), **kw)
        return original_process(self, type_, **kw)

    SQLiteTypeCompiler.process = process  # type: ignore[method-assign]


@pytest.fixture()
def engine():
    """A fresh in-memory sqlite engine per test, with the full schema."""
    from packages.infrastructure.db.base import Base
    from packages.infrastructure.db import models  # noqa: F401  (registers models)

    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def session_factory(engine):
    """A sessionmaker bound to the per-test engine."""
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


@pytest.fixture()
def session(session_factory) -> Iterator[Session]:
    """A single Session for the test to use."""
    s = session_factory()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def tenant_id() -> UUID:
    return uuid4()


@pytest.fixture()
def other_tenant_id() -> UUID:
    return uuid4()


@pytest.fixture()
def make_tenant(session):
    """Factory: create a Tenant row and return it."""

    def _make(name: str = "T", slug: str | None = None) -> "object":
        from packages.infrastructure.db.models.tenants import Tenant

        t = Tenant(
            id=uuid4(),
            name=name,
            slug=slug or f"slug-{name}-{uuid4().hex[:6]}",
            status="trial",
            timezone="UTC",
        )
        session.add(t)
        session.flush()
        return t

    return _make

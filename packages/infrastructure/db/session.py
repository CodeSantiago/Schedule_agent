"""Database engine and session factory.

Designed to be imported by repositories and FastAPI dependencies. The
session is a regular SQLAlchemy `Session` (not async) for this slice —
async wiring can be added later behind the same `get_db()` interface.

The module-level `engine` and `SessionLocal` are built **lazily** so that
importing this module (or `packages.infrastructure.db`) does not require
`DATABASE_URL` to be set, nor the production Postgres driver to be
installed. Tests that need an engine call `make_engine(...)` explicitly.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from typing import Optional

import sqlalchemy as sa
from sqlalchemy import create_engine, inspect as sa_inspect
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from packages.infrastructure.db.base import Base

# Ensure models are imported so `Base.metadata` is fully populated before
# Alembic or any code introspects it.
from packages.infrastructure.db import models  # noqa: F401  (import side-effect)


def _build_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Copy .env.example to .env and "
            "configure a PostgreSQL connection string."
        )
    return url


def make_engine(url: str | None = None, *, echo: bool = False) -> Engine:
    """Create a SQLAlchemy engine.

    `pool_pre_ping` protects against stale connections in long-lived
    services (uvicorn workers, background tasks).
    """
    return create_engine(
        url or _build_database_url(),
        echo=echo,
        pool_pre_ping=True,
        future=True,
    )


_engine: Optional[Engine] = None
_SessionLocal: Optional[sessionmaker[Session]] = None


class _LazyEngine:
    """Proxy that resolves the engine on first attribute access.

    Allows `from packages.infrastructure.db.session import engine` to work
    without immediately requiring `DATABASE_URL` or the production driver.
    """

    def __getattr__(self, item: str) -> Engine:  # pragma: no cover - tiny shim
        return getattr(_get_engine(), item)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<LazyEngine resolved={_engine is not None}>"


class _LazySessionFactory:
    """Proxy that resolves the session factory on first use."""

    def __getattr__(self, item: str) -> sessionmaker[Session]:  # pragma: no cover
        return getattr(_get_session_factory(), item)

    def __call__(self, *args: object, **kwargs: object) -> Session:  # pragma: no cover
        return _get_session_factory()(*args, **kwargs)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<LazySessionFactory resolved={_SessionLocal is not None}>"


def _get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = make_engine()
    return _engine


def _get_session_factory() -> sessionmaker[Session]:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            bind=_get_engine(),
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
            class_=Session,
        )
    return _SessionLocal


def get_engine() -> Engine:
    """Return the lazily-initialised module engine. Kept for back-compat
    with code that imports `engine` directly."""
    return _get_engine()


def get_session_factory() -> sessionmaker[Session]:
    """Return the lazily-initialised session factory."""
    return _get_session_factory()


# Module-level lazy proxies. Code that previously did
# `from packages.infrastructure.db.session import engine, SessionLocal`
# keeps working; the underlying engine is only built when the proxy is
# actually used.
engine: _LazyEngine = _LazyEngine()  # type: ignore[assignment]
SessionLocal: _LazySessionFactory = _LazySessionFactory()  # type: ignore[assignment]


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a transactional session.

    Repositories will receive this session via DI and call `commit()` /
    `rollback()` themselves. If the request handler raises before
    `commit()` is called, we roll back the transaction so the next
    request does not inherit a half-finished one; `close()` is always
    called to release the connection back to the pool.
    """
    db = _get_session_factory()()
    try:
        yield db
    except Exception:
        # Don't mask the original exception with a rollback error. If
        # the rollback itself fails we still want close() to run.
        try:
            db.rollback()
        except Exception:  # pragma: no cover - defensive
            pass
        raise
    finally:
        db.close()


def verify_schema(engine: Engine | None = None) -> list[str]:
    """Verify the database schema matches the model metadata.

    Returns a list of human-readable issues, or an empty list if the
    schema looks correct. Call this during application startup (e.g.
    FastAPI ``lifespan``) to catch schema drift before requests flow.

    Currently checks:

    * ``tenants.location`` column exists (added in migration 0009).
    * For SQLite only: ``conversation_sessions`` CHECK constraint
      covers all ``SESSION_STATE_VALUES`` from the model.
    """
    issues: list[str] = []
    eng = engine or _get_engine()

    try:
        inspector = sa_inspect(eng)

        # ── tenants.location ────────────────────────────────────────────
        try:
            tenant_cols = {c["name"] for c in inspector.get_columns("tenants")}
        except Exception:
            issues.append("tenants table not found — has the schema been created?")
            return issues

        if "location" not in tenant_cols:
            issues.append(
                "tenants.location column is missing. "
                "Run alembic upgrade head to apply migration 0009 / 0010."
            )

        # ── SQLite-only: session_state CHECK constraint coverage ────────
        if eng.dialect.name == "sqlite":
            _check_sqlite_session_state(eng, issues)

    except Exception as exc:
        issues.append(f"Schema verification failed: {exc}")

    return issues


def _check_sqlite_session_state(engine: Engine, issues: list[str]) -> None:
    """Compare SQLite CHECK constraint values against SESSION_STATE_VALUES."""
    from packages.infrastructure.db.models.messaging import SESSION_STATE_VALUES

    with engine.connect() as conn:
        row = conn.execute(
            sa.text(
                "SELECT sql FROM sqlite_master "
                "WHERE type='table' AND name='conversation_sessions'"
            )
        ).scalar()

    if not row:
        issues.append("conversation_sessions table not found.")
        return

    # The CHECK constraint is embedded in the CREATE TABLE DDL.
    # We look for: CHECK (state IN ('val1', 'val2', ..., 'valN'))
    # and extract the values to compare against SESSION_STATE_VALUES.
    import re

    match = re.search(
        r"CHECK\s*\(\s*state\s+IN\s*\(([^)]+)\)\s*\)",
        row,
        re.IGNORECASE,
    )
    if not match:
        issues.append(
            "conversation_sessions.state has no CHECK constraint — "
            "all state values are accepted, consider adding one."
        )
        return

    # Parse the values from the constraint (they are single-quoted).
    raw = match.group(1)
    constrained = set()
    for m in re.finditer(r"'([^']+)'", raw):
        constrained.add(m.group(1))

    model_values = set(SESSION_STATE_VALUES)

    missing = sorted(model_values - constrained)
    if missing:
        issues.append(
            f"SQLite CHECK constraint on conversation_sessions.state is "
            f"missing {len(missing)} state(s): {', '.join(missing)}. "
            f"Run alembic upgrade head to apply migration 0010."
        )

    extra = sorted(constrained - model_values)
    if extra:
        issues.append(
            f"SQLite CHECK constraint on conversation_sessions.state has "
            f"{len(extra)} stale state(s): {', '.join(extra)}. "
            f"This should not happen — the model is the source of truth."
        )

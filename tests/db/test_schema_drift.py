"""Tests that would have caught the SQLite schema drift.

Checks:

1. ``verify_schema()`` returns no issues for a fresh ``create_all()`` schema.
2. ``tenants.location`` is present and accessible.
3. ``conversation_sessions.state`` CHECK constraint on the
   ``create_all()``-built schema includes all ``SESSION_STATE_VALUES``.
4. A manually constructed SQLite schema that simulates the drift from
   migration 0001 (old CHECK constraint) is correctly detected by
   ``verify_schema()``.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine

from packages.infrastructure.db.models.messaging import SESSION_STATE_VALUES


class TestVerifySchema:
    """verify_schema() on a fresh create_all() SQLite schema."""

    def test_returns_no_issues_on_fresh_schema(self, engine_factory) -> None:
        from packages.infrastructure.db.session import verify_schema

        eng = engine_factory()
        issues = verify_schema(eng)
        assert issues == [], f"Expected no issues, got: {issues}"

    def test_detects_missing_location(self, engine_factory) -> None:
        """Simulate a schema missing tenants.location."""
        from packages.infrastructure.db.session import verify_schema

        eng = engine_factory(drop_location=True)
        issues = verify_schema(eng)
        assert any("tenants.location" in issue for issue in issues), (
            f"Expected warning about missing location, got: {issues}"
        )

    def test_detects_missing_state_values(self, engine_factory) -> None:
        """Simulate a schema with the old 0001-only CHECK constraint."""

        from packages.infrastructure.db.session import verify_schema

        eng = engine_factory(with_old_constraint=True)
        issues = verify_schema(eng)
        # Should report missing states added in 0009.
        new_states = {
            "booking_confirmation",
            "booking_cancelled",
            "selecting_new_time",
            "booking_rescheduled",
        }
        found_new = {s for s in new_states if any(s in issue for issue in issues)}
        assert found_new == new_states, (
            f"Expected verify_schema to flag all 4 new states "
            f"(booking_confirmation, booking_cancelled, "
            f"selecting_new_time, booking_rescheduled), "
            f"only flagged: {found_new}"
        )


class TestTenantLocationColumn:
    """The ``tenants.location`` column (added in migration 0009)."""

    def test_column_exists_on_fresh_schema(self, engine_factory) -> None:
        """Location column is present on a create_all() schema."""
        eng = engine_factory()
        from sqlalchemy import inspect

        inspector = inspect(eng)
        cols = {c["name"] for c in inspector.get_columns("tenants")}
        assert "location" in cols, "tenants.location should exist"

    def test_storing_and_reading_location(self, session) -> None:
        """A tenant row can store and retrieve a location."""
        from packages.infrastructure.db.models.tenants import Tenant
        from uuid import uuid4

        t = Tenant(
            id=uuid4(),
            name="Loc Test",
            slug=f"loc-test-{uuid4().hex[:6]}",
            status="trial",
            timezone="UTC",
            location="Buenos Aires, Argentina",
        )
        session.add(t)
        session.flush()
        session.refresh(t)
        assert t.location == "Buenos Aires, Argentina"


class TestSessionStateConstraint:
    """Test that the ``state`` column accepts ALL ``SESSION_STATE_VALUES``."""

    @pytest.mark.parametrize("state", list(SESSION_STATE_VALUES))
    def test_every_state_can_be_persisted(
        self, session, tenant_id, state
    ) -> None:
        """Every value in SESSION_STATE_VALUES writes and reads back."""
        from packages.infrastructure.repositories.messaging import (
            ConversationSessionRepository,
        )

        repo = ConversationSessionRepository(session, tenant_id)
        sess, created = repo.find_or_create(
            f"+5491100000001", initial_state=state
        )
        assert created is True
        assert sess.state == state

        # Also test advance() to confirm update path.
        repo.advance(sess, new_state=state)
        assert sess.state == state

    def test_new_0009_states_are_accepted(self, session, tenant_id) -> None:
        """Explicitly test the 4 states added in migration 0009 — this
        is what failed in production."""
        from packages.infrastructure.repositories.messaging import (
            ConversationSessionRepository,
        )

        repo = ConversationSessionRepository(session, tenant_id)
        for state in (
            "booking_confirmation",
            "booking_cancelled",
            "selecting_new_time",
            "booking_rescheduled",
        ):
            sess, _ = repo.find_or_create(
                f"+5491100{hash(state) % 10_000_000:07d}",
                initial_state=state,
            )
            assert sess.state == state, f"Failed to persist state={state!r}"


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture()
def engine_factory(session):
    """Return a factory that creates SQLite engines with different schema scenarios."""

    def _make(
        *,
        drop_location: bool = False,
        with_old_constraint: bool = False,
    ) -> "Engine":
        from packages.infrastructure.db.base import Base
        from packages.infrastructure.db import models  # noqa: F401

        if with_old_constraint:
            return _engine_with_old_constraint()
        if drop_location:
            return _engine_without_location()
        eng = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(eng)
        return eng

    return _make


def _engine_with_old_constraint():
    """Create an in-memory SQLite DB with the old 0001-style CHECK constraint
    (missing the 4 states added in 0009)."""
    from sqlalchemy import (
        CheckConstraint,
        Column,
        Integer,
        MetaData,
        String,
        Table,
        Text,
        create_engine,
    )
    from sqlalchemy.dialects.postgresql import UUID as PG_UUID

    # Original 14 states from migration 0001 (before 0009).
    old_states = (
        "start",
        "awaiting_menu",
        "awaiting_service",
        "awaiting_day",
        "awaiting_barber",
        "awaiting_time",
        "awaiting_name",
        "booking_confirmed",
        "awaiting_cancellation",
        "awaiting_reschedule",
        "selecting_cancel_appointment",
        "selecting_reschedule_appointment",
        "idle",
        "closed",
    )
    check_sql = "state IN (" + ", ".join(f"'{s}'" for s in old_states) + ")"

    eng = create_engine("sqlite:///:memory:")
    meta = MetaData()

    _ = Table(
        "tenants",
        meta,
        Column("id", PG_UUID(as_uuid=True), primary_key=True),
        Column("name", String(120), nullable=False),
        Column("slug", String(64), nullable=False, unique=True),
        Column("status", String(32), nullable=False, server_default="trial"),
        Column("timezone", String(64), nullable=False, server_default="UTC"),
        Column("location", String(200), nullable=True),
    )

    _ = Table(
        "conversation_sessions",
        meta,
        Column("id", PG_UUID(as_uuid=True), primary_key=True),
        Column("tenant_id", PG_UUID(as_uuid=True), nullable=False),
        Column("channel", String(32), nullable=False, server_default="whatsapp"),
        Column("customer_phone", String(32), nullable=False),
        Column("state", String(32), nullable=False, server_default="start"),
        # Old constraint: only the 14 original states
        CheckConstraint(check_sql, name="session_state"),
        Column("last_message_seq", Integer(), nullable=False, server_default="0"),
        Column("context", Text(), nullable=False, server_default="{}"),
    )

    meta.create_all(eng)
    return eng


def _engine_without_location():
    """Create an in-memory SQLite DB that is missing the ``tenants.location`` column."""
    from sqlalchemy import (
        CheckConstraint,
        Column,
        Integer,
        MetaData,
        String,
        Table,
        Text,
        create_engine,
    )
    from sqlalchemy.dialects.postgresql import UUID as PG_UUID
    from packages.infrastructure.db.models.messaging import SESSION_STATE_VALUES

    values_sql = ", ".join(f"'{v}'" for v in SESSION_STATE_VALUES)
    check_sql = f"state IN ({values_sql})"

    eng = create_engine("sqlite:///:memory:")
    meta = MetaData()

    # tenants table WITHOUT the location column
    _ = Table(
        "tenants",
        meta,
        Column("id", PG_UUID(as_uuid=True), primary_key=True),
        Column("name", String(120), nullable=False),
        Column("slug", String(64), nullable=False, unique=True),
        Column("status", String(32), nullable=False, server_default="trial"),
        Column("timezone", String(64), nullable=False, server_default="UTC"),
    )

    _ = Table(
        "conversation_sessions",
        meta,
        Column("id", PG_UUID(as_uuid=True), primary_key=True),
        Column("tenant_id", PG_UUID(as_uuid=True), nullable=False),
        Column("channel", String(32), nullable=False, server_default="whatsapp"),
        Column("customer_phone", String(32), nullable=False),
        Column("state", String(32), nullable=False, server_default="start"),
        CheckConstraint(check_sql, name="ck_conversation_sessions_session_state"),
        Column("last_message_seq", Integer(), nullable=False, server_default="0"),
        Column("context", Text(), nullable=False, server_default="{}"),
    )

    meta.create_all(eng)
    return eng




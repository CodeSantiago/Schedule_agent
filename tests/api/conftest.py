"""Pytest fixtures for the API-layer tests.

Re-uses the session-scoped sqlite shim from `tests/conftest.py` (it
installs the JSONB / Postgres-ENUM shims once per session), and adds
the following fixtures:

- `api_engine` builds a per-test in-memory sqlite engine with
  `StaticPool` + `check_same_thread=False` so the FastAPI TestClient
  (which runs the route handlers in a worker thread) can see the
  same in-memory database the test seeded.
- `client` returns a FastAPI `TestClient` whose `get_db` dependency
  is overridden to yield a session bound to `api_engine`.
- `seeded` builds a tenant, an active barber with a 10-20 schedule,
  and a Haircut + a HaircutAndBeard service row — enough for the
  route tests to exercise the happy path without re-creating fixtures
  in every test.
- `tenant_token` creates a tenant user for the seeded tenant and
  returns a valid bearer token.
- `auth_header` wraps the token in an Authorization header dict.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import time
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture()
def api_engine():
    """A per-test in-memory sqlite engine that the TestClient worker
    thread can also access.

    `StaticPool` keeps a single shared connection alive so the schema
    created in the main test thread is visible from the worker thread
    the TestClient spins up; `check_same_thread=False` allows that
    cross-thread use. Each test gets a fresh DB so tests don't leak
    state into each other.
    """
    from packages.infrastructure.db.base import Base
    from packages.infrastructure.db import models  # noqa: F401  (registers models)

    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture()
def api_session_factory(api_engine) -> sessionmaker[Session]:
    return sessionmaker(
        bind=api_engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )


@pytest.fixture()
def client(api_session_factory) -> Iterator[TestClient]:
    """A FastAPI TestClient bound to `api_engine`.

    Overrides `get_db` so the route handlers see the same DB the test
    seeded. The session is per-request — handlers commit/close it via
    the existing `get_db` lifecycle.
    """
    from apps.api.src.deps import get_db
    from apps.api.src.main import app

    def _override_get_db() -> Iterator[Session]:
        s = api_session_factory()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _override_get_db
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def seeded(api_session_factory, make_tenant) -> dict[str, Any]:
    """Seed one tenant with an active barber, a 10-20 schedule, and two services.

    Returns a dict so tests can pluck the IDs they need. The Haircut
    service uses the short code `"C"` and the HaircutAndBeard service
    uses the long form `"CORTE_Y_BARBA"` — this is the exact mismatch
    that the Part 2 verification flagged, and the API tests assert
    that BOTH routes now classify it consistently.
    """
    from packages.infrastructure.db.models.scheduling import (
        Barber,
        BarberSchedule,
        Service,
    )
    from packages.infrastructure.db.models.tenants import Tenant

    session = api_session_factory()
    try:
        # Re-implement the `make_tenant` fixture inline because we
        # need the row to live in `api_engine`, not in the engine
        # the root conftest's `make_tenant` uses.
        tenant = Tenant(
            id=uuid4(),
            name="API tests",
            slug=f"api-tests-{uuid4().hex[:6]}",
            status="trial",
            timezone="UTC",
        )
        session.add(tenant)
        session.flush()

        barber = Barber(
            id=uuid4(),
            tenant_id=tenant.id,
            name="API barber",
            restrictions=None,
            is_active=True,
        )
        haircut_service = Service(
            id=uuid4(),
            tenant_id=tenant.id,
            name="Corte",
            code="C",
            duration_minutes=30,
            price_cents=0,
            is_active=True,
        )
        hb_service = Service(
            id=uuid4(),
            tenant_id=tenant.id,
            name="Corte y Barba",
            code="CORTE_Y_BARBA",  # long form — must be treated as CB by both routes
            duration_minutes=60,
            price_cents=0,
            is_active=True,
        )
        schedule = BarberSchedule(
            id=uuid4(),
            barber_id=barber.id,
            weekday="wed",
            start_time=time(10, 0),
            end_time=time(20, 0),
        )
        session.add_all([barber, haircut_service, hb_service, schedule])
        session.commit()
        return {
            "tenant_id": tenant.id,
            "barber_id": barber.id,
            "haircut_service_id": haircut_service.id,
            "cb_id": hb_service.id,
        }
    finally:
        session.close()


@pytest.fixture()
def tenant_token(api_session_factory, seeded) -> str:
    """Create a tenant user for the seeded tenant and return a bearer token."""
    from packages.application.auth import AuthService

    session = api_session_factory()
    try:
        svc = AuthService(session)
        user = svc.create_tenant_user(
            tenant_id=seeded["tenant_id"],
            email="admin@test.local",
            password="test123",
            name="Test Admin",
        )
        # Set role to owner so tests can exercise owner-level permissions.
        user.role = "owner"
        session.flush()
        issued = svc.authenticate_tenant("admin@test.local", "test123", "pytest")
        session.commit()
        return issued.raw
    finally:
        session.close()


@pytest.fixture()
def auth_header(tenant_token) -> dict[str, str]:
    """Authorization header for the seeded tenant's token."""
    return {"Authorization": f"Bearer {tenant_token}"}


@pytest.fixture()
def superadmin_token(api_session_factory) -> str:
    """Create a superadmin user and return a bearer token."""
    from packages.application.auth import AuthService

    session = api_session_factory()
    try:
        svc = AuthService(session)
        svc.create_superadmin("super@test.local", "test123")
        issued = svc.authenticate("super@test.local", "test123", "pytest")
        session.commit()
        return issued.raw
    finally:
        session.close()


@pytest.fixture()
def superadmin_header(superadmin_token) -> dict[str, str]:
    """Authorization header for the superadmin token."""
    return {"Authorization": f"Bearer {superadmin_token}"}

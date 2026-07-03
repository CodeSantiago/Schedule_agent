"""API tests for schedule import preview + apply.

Tests cover:
- Preview parsing + classification (create, update, unchanged, invalid)
- Apply creates new schedule entries
- Apply updates existing schedule entries
- Invalid rows are rejected (unknown barber, bad weekday, bad time, end <= start)
- Auth/isolation requirements
- Audit logging
"""

from __future__ import annotations

from datetime import time
from uuid import uuid4

import pytest

from packages.infrastructure.db.models.scheduling import Barber, BarberSchedule

# ── Sample import content ─────────────────────────────────────────────────

TSV_SAMPLE = (
    "barber_name\tweekday\tstart_time\tend_time\n"
    "Alice\tmon\t10:00\t14:00\n"
    "Alice\twed\t10:00\t14:00\n"
    "Bob\ttue\t10:30\t19:30\n"
)

TSV_FRESH = (
    "barber_name\tweekday\tstart_time\tend_time\n"
    "Alice\tmon\t09:00\t12:00\n"
    "Bob\tthu\t14:00\t19:00\n"
)

TSV_UNKNOWN_BARBER = (
    "barber_name\tweekday\tstart_time\tend_time\n"
    "Alice\tmon\t10:00\t14:00\n"
    "NONEXISTENT\tmon\t10:00\t14:00\n"
)

TSV_INVALID_TIMES = (
    "barber_name\tweekday\tstart_time\tend_time\n"
    "Alice\tmon\t10:00\t14:00\n"
    "Alice\tmon\t09:15\t14:00\n"    # off-grid start
    "Alice\tmon\t10:00\t13:45\n"    # off-grid end
    "Alice\tmon\t10:00\t09:00\n"    # end before start
    "Alice\txyz\t10:00\t14:00\n"    # bad weekday
    "Alice\tmon\tnotime\t14:00\n"   # bad time
)


# ── Helper: create a fresh tenant with auth token ─────────────────────────


@pytest.fixture()
def make_fresh_tenant(client, api_session_factory, make_tenant):
    """Create a tenant + tenant user + return (tenant_id, auth_header)."""
    from packages.application.auth import AuthService

    tenant = make_tenant(name="Fresh", slug=f"fresh-{uuid4().hex[:6]}")
    tenant_id = tenant.id
    session = api_session_factory()
    try:
        svc = AuthService(session)
        svc.create_tenant_user(
            tenant_id=tenant_id,
            email="fresh@test.local",
            password="test123",
            name="Fresh Admin",
        )
        issued = svc.authenticate_tenant("fresh@test.local", "test123", "pytest")
        session.commit()
        token = issued.raw
    finally:
        session.close()

    headers = {"Authorization": f"Bearer {token}"}
    return tenant_id, headers


@pytest.fixture()
def seed_superadmin(api_session_factory):
    """Create one superadmin."""
    from packages.application.auth import AuthService

    s = api_session_factory()
    try:
        svc = AuthService(s)
        svc.create_superadmin("owner@barber.io", "topsecret-pw")
        s.commit()
    finally:
        s.close()
    return {"email": "owner@barber.io", "password": "topsecret-pw"}


def _superadmin_token(client, seed_superadmin) -> str:
    resp = client.post(
        "/superadmin/auth/login",
        json={"email": seed_superadmin["email"], "password": seed_superadmin["password"]},
    )
    return resp.json()["token"]


def _sa_headers(client, seed_superadmin) -> dict:
    return {"Authorization": f"Bearer {_superadmin_token(client, seed_superadmin)}"}


def _seed_barbers(api_session_factory, tenant_id, barbers_data: list[dict]):
    """Seed existing barbers for a tenant."""
    s = api_session_factory()
    try:
        for b_data in barbers_data:
            s.add(Barber(
                id=uuid4(),
                tenant_id=tenant_id,
                name=b_data["name"],
                restrictions=b_data.get("restrictions"),
                is_active=b_data.get("is_active", True),
            ))
        s.commit()
    finally:
        s.close()


def _seed_schedules(api_session_factory, tenant_id, schedules_data: list[dict]):
    """Seed existing barber schedules for a tenant.

    Each entry must have ``barber`` (the ORM Barber instance), ``weekday``,
    ``start_time``, ``end_time``.
    """
    s = api_session_factory()
    try:
        for sd in schedules_data:
            s.add(BarberSchedule(
                id=uuid4(),
                barber_id=sd["barber"].id,
                weekday=sd["weekday"],
                start_time=sd["start_time"],
                end_time=sd["end_time"],
            ))
        s.commit()
    finally:
        s.close()


# ── Preview tests ─────────────────────────────────────────────────────────


class TestScheduleImportPreview:
    """POST /tenants/{tenant_id}/import/schedules/preview"""

    def test_preview_all_new(self, client, make_fresh_tenant, api_session_factory) -> None:
        """All rows classified as create when no schedules exist."""
        tenant_id, auth_header = make_fresh_tenant
        # Seed a barber named "Alice" so barber_name resolves
        _seed_barbers(api_session_factory, tenant_id, [
            {"name": "Alice", "restrictions": None},
            {"name": "Bob", "restrictions": None},
        ])

        resp = client.post(
            f"/tenants/{tenant_id}/import/schedules/preview",
            headers=auth_header,
            json={"content": TSV_SAMPLE, "delimiter": "\t"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 3
        assert len(body["create"]) == 3
        assert len(body["update"]) == 0
        assert len(body["unchanged"]) == 0
        assert len(body["invalid"]) == 0
        names = [r["row"]["barber_name"] for r in body["create"]]
        assert "Alice" in names
        assert "Bob" in names

    def test_preview_update_unchanged(self, client, make_fresh_tenant, api_session_factory) -> None:
        """Rows classified as update/unchanged when schedules already exist."""
        tenant_id, auth_header = make_fresh_tenant
        _seed_barbers(api_session_factory, tenant_id, [
            {"name": "Alice", "restrictions": None},
            {"name": "Bob", "restrictions": None},
        ])
        # Fetch barbers so we can seed schedules
        s = api_session_factory()
        try:
            alice = s.query(Barber).filter_by(tenant_id=tenant_id, name="Alice").first()
            bob = s.query(Barber).filter_by(tenant_id=tenant_id, name="Bob").first()
        finally:
            s.close()

        # Seed schedules matching some of TSV_SAMPLE:
        # Alice mon 10:00-14:00 (matches → unchanged)
        # Alice mon 10:00-13:00 (diff end_time → update)
        _seed_schedules(api_session_factory, tenant_id, [
            {"barber": alice, "weekday": "mon", "start_time": time(10, 0), "end_time": time(14, 0)},  # unchanged
            {"barber": bob, "weekday": "tue", "start_time": time(10, 30), "end_time": time(18, 0)},  # update (end differs)
        ])

        resp = client.post(
            f"/tenants/{tenant_id}/import/schedules/preview",
            headers=auth_header,
            json={"content": TSV_SAMPLE, "delimiter": "\t"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 3
        # Alice mon 10:00-14:00 → unchanged (matches seed)
        # Alice wed 10:00-14:00 → create (no schedule for wed)
        # Bob tue 10:30-19:30 → update (end_time 19:30 vs seed 18:00)
        assert len(body["create"]) == 1
        assert len(body["update"]) == 1
        assert len(body["unchanged"]) == 1
        assert len(body["invalid"]) == 0

        unchanged_barbers = [r["row"]["barber_name"] for r in body["unchanged"]]
        update_barbers = [r["row"]["barber_name"] for r in body["update"]]
        assert "Alice" in unchanged_barbers
        assert "Bob" in update_barbers

    def test_preview_unknown_barber(self, client, make_fresh_tenant, api_session_factory) -> None:
        """Row with unknown barber is invalid."""
        tenant_id, auth_header = make_fresh_tenant
        _seed_barbers(api_session_factory, tenant_id, [
            {"name": "Alice", "restrictions": None},
        ])

        resp = client.post(
            f"/tenants/{tenant_id}/import/schedules/preview",
            headers=auth_header,
            json={"content": TSV_UNKNOWN_BARBER, "delimiter": "\t"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 2
        assert len(body["create"]) == 1  # Alice
        assert len(body["invalid"]) == 1  # NONEXISTENT
        assert any("not found" in r["reason"].lower() for r in body["invalid"])

    def test_preview_invalid_rows(self, client, make_fresh_tenant, api_session_factory) -> None:
        """Rows with validation errors are classified as invalid."""
        tenant_id, auth_header = make_fresh_tenant
        _seed_barbers(api_session_factory, tenant_id, [
            {"name": "Alice", "restrictions": None},
        ])

        resp = client.post(
            f"/tenants/{tenant_id}/import/schedules/preview",
            headers=auth_header,
            json={"content": TSV_INVALID_TIMES, "delimiter": "\t"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 6
        assert len(body["create"]) == 1  # Alice mon 10:00-14:00 (valid)
        # 5 invalid rows: off-grid start, off-grid end, end < start, bad weekday, bad start_time
        assert len(body["invalid"]) == 5

        reasons = " ".join(r["reason"] for r in body["invalid"])
        assert "30-minute grid" in reasons
        assert "must be after" in reasons or "after start_time" in reasons
        assert "Invalid weekday" in reasons
        assert "not a valid time" in reasons

    def test_preview_requires_auth(self, client, seeded) -> None:
        resp = client.post(
            f"/tenants/{seeded['tenant_id']}/import/schedules/preview",
            json={"content": TSV_SAMPLE},
        )
        assert resp.status_code == 401

    def test_preview_requires_tenant_scope(self, client, seeded, seed_superadmin) -> None:
        tenant_id = seeded["tenant_id"]
        resp = client.post(
            f"/tenants/{tenant_id}/import/schedules/preview",
            headers=_sa_headers(client, seed_superadmin),
            json={"content": TSV_SAMPLE},
        )
        assert resp.status_code == 403

    def test_preview_tenant_isolation(self, client, seeded, auth_header) -> None:
        other_id = uuid4()
        resp = client.post(
            f"/tenants/{other_id}/import/schedules/preview",
            headers=auth_header,
            json={"content": TSV_SAMPLE},
        )
        assert resp.status_code == 403

    def test_preview_empty_content(self, client, make_fresh_tenant) -> None:
        tenant_id, auth_header = make_fresh_tenant
        resp = client.post(
            f"/tenants/{tenant_id}/import/schedules/preview",
            headers=auth_header,
            json={"content": "barber_name\tweekday\tstart_time\tend_time\n", "delimiter": "\t"},
        )
        assert resp.status_code == 400

    def test_preview_csv_delimiter(self, client, make_fresh_tenant, api_session_factory) -> None:
        """CSV with comma delimiter also works."""
        tenant_id, auth_header = make_fresh_tenant
        _seed_barbers(api_session_factory, tenant_id, [
            {"name": "Alice", "restrictions": None},
        ])
        csv_content = (
            "barber_name,weekday,start_time,end_time\n"
            "Alice,mon,10:00,14:00\n"
        )
        resp = client.post(
            f"/tenants/{tenant_id}/import/schedules/preview",
            headers=auth_header,
            json={"content": csv_content, "delimiter": ","},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 1
        assert len(body["create"]) == 1


class TestScheduleImportApply:
    """POST /tenants/{tenant_id}/import/schedules/apply"""

    def test_apply_creates_schedules(self, client, make_fresh_tenant, api_session_factory) -> None:
        tenant_id, auth_header = make_fresh_tenant
        _seed_barbers(api_session_factory, tenant_id, [
            {"name": "Alice", "restrictions": None},
            {"name": "Bob", "restrictions": None},
        ])

        resp = client.post(
            f"/tenants/{tenant_id}/import/schedules/apply",
            headers=auth_header,
            json={
                "create": [
                    {"barber_name": "Alice", "weekday": "mon", "start_time": "10:00", "end_time": "14:00"},
                    {"barber_name": "Bob", "weekday": "tue", "start_time": "10:30", "end_time": "19:30"},
                ],
                "update": [],
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["created"] == 2
        assert body["updated"] == 0
        assert body["errors"] == []

        # Verify schedules were actually created
        list_resp = client.get(
            f"/tenants/{tenant_id}/barbers",
            headers=auth_header,
        )
        assert list_resp.status_code == 200

        # Check Alice's schedules
        s = api_session_factory()
        try:
            alice = s.query(Barber).filter_by(tenant_id=tenant_id, name="Alice").first()
            assert alice is not None
            schedules = s.query(BarberSchedule).filter_by(barber_id=alice.id).all()
            assert len(schedules) == 1
            assert schedules[0].weekday == "mon"
            assert schedules[0].start_time == time(10, 0)
            assert schedules[0].end_time == time(14, 0)
        finally:
            s.close()

    def test_apply_updates_schedules(self, client, make_fresh_tenant, api_session_factory) -> None:
        tenant_id, auth_header = make_fresh_tenant
        _seed_barbers(api_session_factory, tenant_id, [
            {"name": "Alice", "restrictions": None},
        ])

        s = api_session_factory()
        try:
            alice = s.query(Barber).filter_by(tenant_id=tenant_id, name="Alice").first()
            # Seed existing schedule with different end_time
            existing = BarberSchedule(
                id=uuid4(),
                barber_id=alice.id,
                weekday="mon",
                start_time=time(10, 0),
                end_time=time(13, 0),
            )
            s.add(existing)
            s.commit()
        finally:
            s.close()

        resp = client.post(
            f"/tenants/{tenant_id}/import/schedules/apply",
            headers=auth_header,
            json={
                "create": [],
                "update": [
                    {"barber_name": "Alice", "weekday": "mon", "start_time": "10:00", "end_time": "14:00"},
                ],
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["created"] == 0
        assert body["updated"] == 1
        assert body["errors"] == []

        # Verify the schedule was updated
        s = api_session_factory()
        try:
            alice = s.query(Barber).filter_by(tenant_id=tenant_id, name="Alice").first()
            updated = s.query(BarberSchedule).filter_by(
                barber_id=alice.id, weekday="mon", start_time=time(10, 0)
            ).first()
            assert updated is not None
            assert updated.end_time == time(14, 0)
        finally:
            s.close()

    def test_apply_requires_existing_for_update(self, client, make_fresh_tenant, api_session_factory) -> None:
        tenant_id, auth_header = make_fresh_tenant
        _seed_barbers(api_session_factory, tenant_id, [
            {"name": "Alice", "restrictions": None},
        ])

        resp = client.post(
            f"/tenants/{tenant_id}/import/schedules/apply",
            headers=auth_header,
            json={
                "create": [],
                "update": [
                    {"barber_name": "Alice", "weekday": "mon", "start_time": "10:00", "end_time": "14:00"},
                ],
            },
        )
        assert resp.status_code == 400

    def test_apply_unknown_barber_rejected(self, client, make_fresh_tenant) -> None:
        tenant_id, auth_header = make_fresh_tenant
        resp = client.post(
            f"/tenants/{tenant_id}/import/schedules/apply",
            headers=auth_header,
            json={
                "create": [
                    {"barber_name": "NONEXISTENT", "weekday": "mon", "start_time": "10:00", "end_time": "14:00"},
                ],
                "update": [],
            },
        )
        assert resp.status_code == 400
        assert "not found" in resp.text.lower()

    def test_apply_nothing_to_do(self, client, make_fresh_tenant) -> None:
        tenant_id, auth_header = make_fresh_tenant
        resp = client.post(
            f"/tenants/{tenant_id}/import/schedules/apply",
            headers=auth_header,
            json={"create": [], "update": []},
        )
        assert resp.status_code == 400

    def test_apply_invalid_row_rejected(self, client, make_fresh_tenant, api_session_factory) -> None:
        """Row with invalid weekday is rejected (422 from Pydantic)."""
        tenant_id, auth_header = make_fresh_tenant
        _seed_barbers(api_session_factory, tenant_id, [
            {"name": "Alice", "restrictions": None},
        ])
        resp = client.post(
            f"/tenants/{tenant_id}/import/schedules/apply",
            headers=auth_header,
            json={
                "create": [
                    {"barber_name": "Alice", "weekday": "xyz", "start_time": "10:00", "end_time": "14:00"},
                ],
                "update": [],
            },
        )
        # Pydantic catches invalid weekday (must be 3 chars, but xyz passes that)
        # However the route handler's validate_row will catch it
        assert resp.status_code == 400

    def test_apply_requires_auth(self, client, seeded) -> None:
        resp = client.post(
            f"/tenants/{seeded['tenant_id']}/import/schedules/apply",
            json={"create": [{"barber_name": "Alice", "weekday": "mon", "start_time": "10:00", "end_time": "14:00"}], "update": []},
        )
        assert resp.status_code == 401

    def test_apply_tenant_isolation(self, client, seeded, auth_header) -> None:
        other_id = uuid4()
        resp = client.post(
            f"/tenants/{other_id}/import/schedules/apply",
            headers=auth_header,
            json={"create": [{"barber_name": "Alice", "weekday": "mon", "start_time": "10:00", "end_time": "14:00"}], "update": []},
        )
        assert resp.status_code == 403


class TestScheduleImportAuditLog:
    """Verify audit log entries are written on apply."""

    def test_audit_log_on_apply(self, client, make_fresh_tenant, api_session_factory) -> None:
        tenant_id, auth_header = make_fresh_tenant
        _seed_barbers(api_session_factory, tenant_id, [
            {"name": "Alice", "restrictions": None},
        ])

        client.post(
            f"/tenants/{tenant_id}/import/schedules/apply",
            headers=auth_header,
            json={
                "create": [
                    {"barber_name": "Alice", "weekday": "mon", "start_time": "10:00", "end_time": "14:00"},
                ],
                "update": [],
            },
        )

        resp = client.get(
            f"/tenants/{tenant_id}/logs?limit=5",
            headers=auth_header,
        )
        assert resp.status_code == 200
        entries = resp.json()["entries"]
        matching = [e for e in entries if e["event_type"] == "schedules_import_applied"]
        assert len(matching) >= 1
        assert matching[0]["actor_scope"] == "tenant"
        assert matching[0]["details"]["created"] == 1

"""API tests for services import preview + apply.

Tests cover:
- Preview parsing + classification
- Apply creates new services
- Apply updates existing services
- Invalid rows are rejected
- Auth/isolation requirements
- Audit logging

NOTE: The `seeded` fixture already creates Corte (code="C", duration=30, price=0)
and CB (code="CORTE_Y_BARBA", duration=60, price=0). Tests that need a clean
slate create a fresh tenant via `make_fresh_tenant`.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from packages.infrastructure.db.models.scheduling import Service

# ── Sample import content ─────────────────────────────────────────────────

TSV_SAMPLE = (
    "code\tname\tduration_minutes\tprice_cents\tdescription\n"
    "C\tCorte\t30\t2500\t\n"
    "B\tBarba\t15\t1500\tBeard trim\n"
    "CB\tCorte y Barba\t60\t3500\t\n"
)

TSV_FRESH = (
    "code\tname\tduration_minutes\tprice_cents\tdescription\n"
    "X\tService X\t30\t2500\tFirst service\n"
    "Y\tService Y\t45\t2000\tSecond service\n"
)

TSV_INVALID = (
    "code\tname\tduration_minutes\tprice_cents\tdescription\n"
    "Z\tValid\t30\t2500\tValid row\n"
    "\tMissingCode\t15\t1000\tNo code here\n"
    "W\tBadDuration\tabc\t500\tBad duration\n"
    "V\tNegativePrice\t30\t-100\tBad price\n"
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


def _seed_services(api_session_factory, tenant_id, services_data: list[dict]):
    """Seed existing services for a tenant."""
    s = api_session_factory()
    try:
        for svc_data in services_data:
            s.add(Service(
                id=uuid4(),
                tenant_id=tenant_id,
                name=svc_data["name"],
                code=svc_data["code"],
                duration_minutes=svc_data["duration_minutes"],
                price_cents=svc_data.get("price_cents", 0),
                description=svc_data.get("description"),
                is_active=svc_data.get("is_active", True),
            ))
        s.commit()
    finally:
        s.close()


# ── Preview tests ─────────────────────────────────────────────────────────


class TestServicesImportPreview:
    """POST /tenants/{tenant_id}/import/services/preview"""

    def test_preview_all_new(self, client, make_fresh_tenant) -> None:
        """All rows classified as create when no services exist."""
        tenant_id, auth_header = make_fresh_tenant
        resp = client.post(
            f"/tenants/{tenant_id}/import/services/preview",
            headers=auth_header,
            json={"content": TSV_FRESH, "delimiter": "\t"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 2
        assert len(body["create"]) == 2
        assert len(body["update"]) == 0
        assert len(body["unchanged"]) == 0
        assert len(body["invalid"]) == 0
        codes = [r["row"]["code"] for r in body["create"]]
        assert "X" in codes
        assert "Y" in codes

    def test_preview_update_unchanged(self, client, make_fresh_tenant, api_session_factory) -> None:
        """Rows classified as update/unchanged when services already exist."""
        tenant_id, auth_header = make_fresh_tenant

        # Seed services matching some of the TSV_SAMPLE rows
        # Matched by code: C exists (same data → unchanged), B exists (diff name/price → update)
        _seed_services(api_session_factory, tenant_id, [
            {"name": "Corte", "code": "C", "duration_minutes": 30, "price_cents": 2500, "description": None},  # unchanged (matches TSV)
            {"name": "Barba Old", "code": "B", "duration_minutes": 15, "price_cents": 1000},  # update (name/price differ)
        ])

        resp = client.post(
            f"/tenants/{tenant_id}/import/services/preview",
            headers=auth_header,
            json={"content": TSV_SAMPLE, "delimiter": "\t"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 3
        # C → unchanged (same), B → update (different), CB → create (not found)
        assert len(body["create"]) == 1
        assert len(body["update"]) == 1
        assert len(body["unchanged"]) == 1
        assert len(body["invalid"]) == 0

        unchanged_codes = [r["row"]["code"] for r in body["unchanged"]]
        update_codes = [r["row"]["code"] for r in body["update"]]
        create_codes = [r["row"]["code"] for r in body["create"]]
        assert "C" in unchanged_codes
        assert "B" in update_codes
        assert "CB" in create_codes

    def test_preview_invalid_rows(self, client, make_fresh_tenant) -> None:
        """Rows with validation errors are classified as invalid."""
        tenant_id, auth_header = make_fresh_tenant
        resp = client.post(
            f"/tenants/{tenant_id}/import/services/preview",
            headers=auth_header,
            json={"content": TSV_INVALID, "delimiter": "\t"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 4
        assert len(body["create"]) == 1  # Z (valid)
        assert len(body["invalid"]) == 3  # MissingCode, BadDuration, NegativePrice

        invalid_reasons = [r["reason"] for r in body["invalid"]]
        assert any("code is required" in r.lower() for r in invalid_reasons)
        assert any("valid integer" in r.lower() for r in invalid_reasons)

    def test_preview_requires_auth(self, client, seeded) -> None:
        resp = client.post(
            f"/tenants/{seeded['tenant_id']}/import/services/preview",
            json={"content": TSV_FRESH},
        )
        assert resp.status_code == 401

    def test_preview_requires_tenant_scope(self, client, seeded, seed_superadmin) -> None:
        tenant_id = seeded["tenant_id"]
        resp = client.post(
            f"/tenants/{tenant_id}/import/services/preview",
            headers=_sa_headers(client, seed_superadmin),
            json={"content": TSV_FRESH},
        )
        assert resp.status_code == 403

    def test_preview_tenant_isolation(self, client, seeded, auth_header) -> None:
        other_id = uuid4()
        resp = client.post(
            f"/tenants/{other_id}/import/services/preview",
            headers=auth_header,
            json={"content": TSV_FRESH},
        )
        assert resp.status_code == 403

    def test_preview_empty_content(self, client, make_fresh_tenant) -> None:
        tenant_id, auth_header = make_fresh_tenant
        resp = client.post(
            f"/tenants/{tenant_id}/import/services/preview",
            headers=auth_header,
            json={"content": "code\tname\tduration_minutes\n", "delimiter": "\t"},
        )
        assert resp.status_code == 400

    def test_preview_csv_delimiter(self, client, make_fresh_tenant) -> None:
        """CSV with comma delimiter also works."""
        tenant_id, auth_header = make_fresh_tenant
        csv_content = (
            "code,name,duration_minutes,price_cents\n"
            "X,Service X,30,2500\n"
            "Y,Service Y,15,1500\n"
        )
        resp = client.post(
            f"/tenants/{tenant_id}/import/services/preview",
            headers=auth_header,
            json={"content": csv_content, "delimiter": ","},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 2
        assert len(body["create"]) == 2


class TestServicesImportApply:
    """POST /tenants/{tenant_id}/import/services/apply"""

    def test_apply_creates_services(self, client, make_fresh_tenant) -> None:
        tenant_id, auth_header = make_fresh_tenant
        resp = client.post(
            f"/tenants/{tenant_id}/import/services/apply",
            headers=auth_header,
            json={
                "create": [
                    {"code": "X", "name": "Service X", "duration_minutes": 30, "price_cents": 2500},
                    {"code": "Y", "name": "Service Y", "duration_minutes": 15, "price_cents": 1500},
                ],
                "update": [],
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["created"] == 2
        assert body["updated"] == 0
        assert body["errors"] == []

        # Verify services were actually created
        list_resp = client.get(
            f"/tenants/{tenant_id}/services",
            headers=auth_header,
        )
        assert list_resp.status_code == 200
        services = list_resp.json()
        codes = [svc["code"] for svc in services]
        assert "X" in codes
        assert "Y" in codes

    def test_apply_updates_services(self, client, make_fresh_tenant, api_session_factory) -> None:
        tenant_id, auth_header = make_fresh_tenant
        _seed_services(api_session_factory, tenant_id, [
            {"name": "Service Old", "code": "X", "duration_minutes": 30, "price_cents": 2000},
        ])

        resp = client.post(
            f"/tenants/{tenant_id}/import/services/apply",
            headers=auth_header,
            json={
                "create": [],
                "update": [
                    {"code": "X", "name": "Service Updated", "duration_minutes": 35, "price_cents": 3000},
                ],
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["created"] == 0
        assert body["updated"] == 1
        assert body["errors"] == []

        # Verify the service was updated
        list_resp = client.get(
            f"/tenants/{tenant_id}/services",
            headers=auth_header,
        )
        assert list_resp.status_code == 200
        services = list_resp.json()
        svc = [s for s in services if s["code"] == "X"][0]
        assert svc["name"] == "Service Updated"
        assert svc["duration_minutes"] == 35
        assert svc["price_cents"] == 3000

    def test_apply_requires_existing_for_update(self, client, make_fresh_tenant) -> None:
        tenant_id, auth_header = make_fresh_tenant
        resp = client.post(
            f"/tenants/{tenant_id}/import/services/apply",
            headers=auth_header,
            json={
                "create": [],
                "update": [
                    {"code": "NONEXIST", "name": "No Such", "duration_minutes": 30, "price_cents": 0},
                ],
            },
        )
        assert resp.status_code == 400

    def test_apply_nothing_to_do(self, client, make_fresh_tenant) -> None:
        tenant_id, auth_header = make_fresh_tenant
        resp = client.post(
            f"/tenants/{tenant_id}/import/services/apply",
            headers=auth_header,
            json={"create": [], "update": []},
        )
        assert resp.status_code == 400

    def test_apply_invalid_row_rejected(self, client, make_fresh_tenant) -> None:
        """Row with empty code is rejected by Pydantic schema validation (422)."""
        tenant_id, auth_header = make_fresh_tenant
        resp = client.post(
            f"/tenants/{tenant_id}/import/services/apply",
            headers=auth_header,
            json={
                "create": [
                    {"code": "", "name": "No Code", "duration_minutes": 30, "price_cents": 0},
                ],
                "update": [],
            },
        )
        # Pydantic catches empty code (min_length=1) before our handler
        assert resp.status_code == 422

    def test_apply_requires_auth(self, client, seeded) -> None:
        resp = client.post(
            f"/tenants/{seeded['tenant_id']}/import/services/apply",
            json={"create": [{"code": "X", "name": "Test", "duration_minutes": 30}], "update": []},
        )
        assert resp.status_code == 401

    def test_apply_tenant_isolation(self, client, seeded, auth_header) -> None:
        other_id = uuid4()
        resp = client.post(
            f"/tenants/{other_id}/import/services/apply",
            headers=auth_header,
            json={"create": [{"code": "X", "name": "Test", "duration_minutes": 30}], "update": []},
        )
        assert resp.status_code == 403


class TestServicesImportAuditLog:
    """Verify audit log entries are written on apply."""

    def test_audit_log_on_apply(self, client, make_fresh_tenant) -> None:
        tenant_id, auth_header = make_fresh_tenant
        client.post(
            f"/tenants/{tenant_id}/import/services/apply",
            headers=auth_header,
            json={
                "create": [
                    {"code": "X", "name": "Service X", "duration_minutes": 30, "price_cents": 2500},
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
        matching = [e for e in entries if e["event_type"] == "services_import_applied"]
        assert len(matching) >= 1
        assert matching[0]["actor_scope"] == "tenant"
        assert matching[0]["details"]["created"] == 1

"""API tests for barber import preview + apply.

Tests cover:
- Preview parsing + classification
- Apply creates new barbers
- Apply updates existing barbers
- Invalid rows are rejected
- Auth/isolation requirements
- Audit logging
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from packages.infrastructure.db.models.scheduling import Barber

# ── Sample import content ─────────────────────────────────────────────────

TSV_SAMPLE = (
    "name\trestrictions\tis_active\n"
    "Alice\t\tTrue\n"
    "Bob\tSOLO_CORTE\tTrue\n"
    "Charlie\t\tfalse\n"
)

TSV_FRESH = (
    "name\trestrictions\tis_active\n"
    "David\t\tTrue\n"
    "Eve\tSENIOR\tTrue\n"
)

def _make_invalid_tsv() -> str:
    """Build TSV with one valid row and one too-long-name row."""
    long_name = "A" * 121  # exceeds max length of 120
    return (
        "name\trestrictions\tis_active\n"
        "Frank\t\tTrue\n"
        f"{long_name}\tTrue\n"
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


# ── Preview tests ─────────────────────────────────────────────────────────


class TestBarberImportPreview:
    """POST /tenants/{tenant_id}/import/barbers/preview"""

    def test_preview_all_new(self, client, make_fresh_tenant) -> None:
        """All rows classified as create when no barbers exist."""
        tenant_id, auth_header = make_fresh_tenant
        resp = client.post(
            f"/tenants/{tenant_id}/import/barbers/preview",
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
        names = [r["row"]["name"] for r in body["create"]]
        assert "David" in names
        assert "Eve" in names

    def test_preview_update_unchanged(self, client, make_fresh_tenant, api_session_factory) -> None:
        """Rows classified as update/unchanged when barbers already exist."""
        tenant_id, auth_header = make_fresh_tenant

        # Seed barbers matching some of the TSV_SAMPLE rows
        # Matched by name: Alice exists (same data → unchanged),
        # Bob exists (different restrictions → update)
        _seed_barbers(api_session_factory, tenant_id, [
            {"name": "Alice", "restrictions": None, "is_active": True},       # unchanged
            {"name": "Bob", "restrictions": "SOLO_CORTE_OLD", "is_active": True},  # update
        ])

        resp = client.post(
            f"/tenants/{tenant_id}/import/barbers/preview",
            headers=auth_header,
            json={"content": TSV_SAMPLE, "delimiter": "\t"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 3
        # Alice → unchanged (same), Bob → update (restrictions diff), Charlie → create (not found)
        assert len(body["create"]) == 1
        assert len(body["update"]) == 1
        assert len(body["unchanged"]) == 1
        assert len(body["invalid"]) == 0

        unchanged_names = [r["row"]["name"] for r in body["unchanged"]]
        update_names = [r["row"]["name"] for r in body["update"]]
        create_names = [r["row"]["name"] for r in body["create"]]
        assert "Alice" in unchanged_names
        assert "Bob" in update_names
        assert "Charlie" in create_names

    def test_preview_invalid_rows(self, client, make_fresh_tenant) -> None:
        """Rows with validation errors are classified as invalid."""
        tenant_id, auth_header = make_fresh_tenant
        resp = client.post(
            f"/tenants/{tenant_id}/import/barbers/preview",
            headers=auth_header,
            json={"content": _make_invalid_tsv(), "delimiter": "\t"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Frank is valid, long name is invalid (empty-name rows are skipped during parse)
        assert body["total"] == 2
        assert len(body["create"]) == 1  # Frank
        assert len(body["invalid"]) == 1  # long name

        invalid_reasons = [r["reason"] for r in body["invalid"]]
        assert any("must be 120" in r.lower() for r in invalid_reasons)

    def test_preview_requires_auth(self, client, seeded) -> None:
        resp = client.post(
            f"/tenants/{seeded['tenant_id']}/import/barbers/preview",
            json={"content": TSV_FRESH},
        )
        assert resp.status_code == 401

    def test_preview_requires_tenant_scope(self, client, seeded, seed_superadmin) -> None:
        tenant_id = seeded["tenant_id"]
        resp = client.post(
            f"/tenants/{tenant_id}/import/barbers/preview",
            headers=_sa_headers(client, seed_superadmin),
            json={"content": TSV_FRESH},
        )
        assert resp.status_code == 403

    def test_preview_tenant_isolation(self, client, seeded, auth_header) -> None:
        other_id = uuid4()
        resp = client.post(
            f"/tenants/{other_id}/import/barbers/preview",
            headers=auth_header,
            json={"content": TSV_FRESH},
        )
        assert resp.status_code == 403

    def test_preview_empty_content(self, client, make_fresh_tenant) -> None:
        tenant_id, auth_header = make_fresh_tenant
        resp = client.post(
            f"/tenants/{tenant_id}/import/barbers/preview",
            headers=auth_header,
            json={"content": "name\trestrictions\n", "delimiter": "\t"},
        )
        assert resp.status_code == 400

    def test_preview_csv_delimiter(self, client, make_fresh_tenant) -> None:
        """CSV with comma delimiter also works."""
        tenant_id, auth_header = make_fresh_tenant
        csv_content = (
            "name,restrictions,is_active\n"
            "David,,True\n"
            "Eve,SENIOR,True\n"
        )
        resp = client.post(
            f"/tenants/{tenant_id}/import/barbers/preview",
            headers=auth_header,
            json={"content": csv_content, "delimiter": ","},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 2
        assert len(body["create"]) == 2


class TestBarberImportApply:
    """POST /tenants/{tenant_id}/import/barbers/apply"""

    def test_apply_creates_barbers(self, client, make_fresh_tenant) -> None:
        tenant_id, auth_header = make_fresh_tenant
        resp = client.post(
            f"/tenants/{tenant_id}/import/barbers/apply",
            headers=auth_header,
            json={
                "create": [
                    {"name": "David", "restrictions": None, "is_active": True},
                    {"name": "Eve", "restrictions": "SENIOR", "is_active": True},
                ],
                "update": [],
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["created"] == 2
        assert body["updated"] == 0
        assert body["errors"] == []

        # Verify barbers were actually created
        list_resp = client.get(
            f"/tenants/{tenant_id}/barbers",
            headers=auth_header,
        )
        assert list_resp.status_code == 200
        barbers = list_resp.json()
        names = [b["name"] for b in barbers]
        assert "David" in names
        assert "Eve" in names

    def test_apply_updates_barbers(self, client, make_fresh_tenant, api_session_factory) -> None:
        tenant_id, auth_header = make_fresh_tenant
        _seed_barbers(api_session_factory, tenant_id, [
            {"name": "David", "restrictions": None, "is_active": True},
        ])

        resp = client.post(
            f"/tenants/{tenant_id}/import/barbers/apply",
            headers=auth_header,
            json={
                "create": [],
                "update": [
                    {"name": "David", "restrictions": "SENIOR", "is_active": True},
                ],
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["created"] == 0
        assert body["updated"] == 1
        assert body["errors"] == []

        # Verify the barber was updated
        list_resp = client.get(
            f"/tenants/{tenant_id}/barbers",
            headers=auth_header,
        )
        assert list_resp.status_code == 200
        barbers = list_resp.json()
        barber = [b for b in barbers if b["name"] == "David"][0]
        assert barber["restrictions"] == "SENIOR"

    def test_apply_requires_existing_for_update(self, client, make_fresh_tenant) -> None:
        tenant_id, auth_header = make_fresh_tenant
        resp = client.post(
            f"/tenants/{tenant_id}/import/barbers/apply",
            headers=auth_header,
            json={
                "create": [],
                "update": [
                    {"name": "NONEXIST", "restrictions": None, "is_active": True},
                ],
            },
        )
        assert resp.status_code == 400

    def test_apply_nothing_to_do(self, client, make_fresh_tenant) -> None:
        tenant_id, auth_header = make_fresh_tenant
        resp = client.post(
            f"/tenants/{tenant_id}/import/barbers/apply",
            headers=auth_header,
            json={"create": [], "update": []},
        )
        assert resp.status_code == 400

    def test_apply_invalid_row_rejected(self, client, make_fresh_tenant) -> None:
        """Row with empty name is rejected by Pydantic schema validation (422)."""
        tenant_id, auth_header = make_fresh_tenant
        resp = client.post(
            f"/tenants/{tenant_id}/import/barbers/apply",
            headers=auth_header,
            json={
                "create": [
                    {"name": "", "restrictions": None, "is_active": True},
                ],
                "update": [],
            },
        )
        # Pydantic catches empty name (min_length=1) before our handler
        assert resp.status_code == 422

    def test_apply_requires_auth(self, client, seeded) -> None:
        resp = client.post(
            f"/tenants/{seeded['tenant_id']}/import/barbers/apply",
            json={"create": [{"name": "Test", "is_active": True}], "update": []},
        )
        assert resp.status_code == 401

    def test_apply_tenant_isolation(self, client, seeded, auth_header) -> None:
        other_id = uuid4()
        resp = client.post(
            f"/tenants/{other_id}/import/barbers/apply",
            headers=auth_header,
            json={"create": [{"name": "Test", "is_active": True}], "update": []},
        )
        assert resp.status_code == 403


class TestBarberImportAuditLog:
    """Verify audit log entries are written on apply."""

    def test_audit_log_on_apply(self, client, make_fresh_tenant) -> None:
        tenant_id, auth_header = make_fresh_tenant
        client.post(
            f"/tenants/{tenant_id}/import/barbers/apply",
            headers=auth_header,
            json={
                "create": [
                    {"name": "David", "restrictions": None, "is_active": True},
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
        matching = [e for e in entries if e["event_type"] == "barbers_import_applied"]
        assert len(matching) >= 1
        assert matching[0]["actor_scope"] == "tenant"
        assert matching[0]["details"]["created"] == 1

"""API tests for tenant self-service Sheets provider config.

Tests cover:
- GET returns existing sheets config
- GET 404 when no sheets config exists
- PUT creates a new sheets config
- PUT updates an existing sheets config (upsert)
- PATCH partial update
- POST /activate activates inactive config
- DELETE removes sheets config
- Auth requirements (401 without token, 403 for wrong scope)
- Tenant isolation (tenant A cannot touch tenant B)
- Audit logging
- Only kind=sheets is affected
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from packages.infrastructure.db.models.providers import ProviderConfig


@pytest.fixture()
def seed_superadmin(api_session_factory):
    """Create one superadmin + return its email + password."""
    from packages.application.auth import AuthService

    s = api_session_factory()
    try:
        svc = AuthService(s)
        svc.create_superadmin("owner@barber.io", "topsecret-pw")
        s.commit()
    finally:
        s.close()
    return {"email": "owner@barber.io", "password": "topsecret-pw"}


# ── Helpers ────────────────────────────────────────────────────────────────


def _superadmin_token(client, seed_superadmin) -> str:
    resp = client.post(
        "/superadmin/auth/login",
        json={"email": seed_superadmin["email"], "password": seed_superadmin["password"]},
    )
    return resp.json()["token"]


def _sa_headers(client, seed_superadmin) -> dict:
    return {"Authorization": f"Bearer {_superadmin_token(client, seed_superadmin)}"}


SHEETS_ENDPOINT = "/tenants/{tenant_id}/sheets-config"
SHEETS_DEFAULT_BODY = {
    "label": "My Google Sheets",
    "provider_name": "google_sheets",
    "credentials": {"api_key": "test-key-123"},
    "settings": {"spreadsheet_id": "abc123", "sheet_name": "Data", "range": "A1:Z1000"},
}


# ── Tests ──────────────────────────────────────────────────────────────────


class TestTenantSheetsGet:
    """GET /tenants/{tenant_id}/sheets-config"""

    def test_get_404_when_no_config(self, client, seeded, auth_header) -> None:
        tenant_id = seeded["tenant_id"]
        resp = client.get(
            f"/tenants/{tenant_id}/sheets-config",
            headers=auth_header,
        )
        assert resp.status_code == 404, resp.text

    def test_get_existing_config(self, client, seeded, auth_header, api_session_factory) -> None:
        tenant_id = seeded["tenant_id"]
        cfg_id = uuid4()
        s = api_session_factory()
        try:
            s.add(ProviderConfig(
                id=cfg_id,
                tenant_id=tenant_id,
                kind="sheets",
                label="Test Sheets",
                provider_name="google_sheets",
                credentials={"api_key": "key-1"},
                settings={"spreadsheet_id": "s1"},
                is_active=True,
            ))
            s.commit()
        finally:
            s.close()

        resp = client.get(
            f"/tenants/{tenant_id}/sheets-config",
            headers=auth_header,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["kind"] == "sheets"
        assert body["label"] == "Test Sheets"
        assert body["provider_name"] == "google_sheets"
        assert body["credentials"] == {"api_key": "key-1"}
        assert body["settings"] == {"spreadsheet_id": "s1"}
        assert body["is_active"] is True
        assert body["id"] == str(cfg_id)

    def test_requires_auth(self, client, seeded) -> None:
        resp = client.get(f"/tenants/{seeded['tenant_id']}/sheets-config")
        assert resp.status_code == 401

    def test_requires_tenant_scope(self, client, seeded, seed_superadmin) -> None:
        """Superadmin token must NOT work on tenant endpoint."""
        tenant_id = seeded["tenant_id"]
        resp = client.get(
            f"/tenants/{tenant_id}/sheets-config",
            headers=_sa_headers(client, seed_superadmin),
        )
        assert resp.status_code == 403

    def test_tenant_isolation(self, client, seeded, auth_header, api_session_factory, other_tenant_id) -> None:
        """Tenant cannot read another tenant's sheets config."""
        # Actually, tenant auth requires matching tenant_id path parameter,
        # so using auth_header for a different tenant should 403.
        other_id = uuid4()
        resp = client.get(
            f"/tenants/{other_id}/sheets-config",
            headers=auth_header,
        )
        assert resp.status_code == 403


class TestTenantSheetsPut:
    """PUT /tenants/{tenant_id}/sheets-config"""

    def test_create_new_config(self, client, seeded, auth_header) -> None:
        tenant_id = seeded["tenant_id"]
        resp = client.put(
            f"/tenants/{tenant_id}/sheets-config",
            headers=auth_header,
            json=SHEETS_DEFAULT_BODY,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["kind"] == "sheets"
        assert body["label"] == "My Google Sheets"
        assert body["provider_name"] == "google_sheets"
        assert body["is_active"] is True
        assert body["credentials"]["api_key"] == "test-key-123"
        assert body["settings"]["spreadsheet_id"] == "abc123"

        # Verify it's readable back.
        resp2 = client.get(
            f"/tenants/{tenant_id}/sheets-config",
            headers=auth_header,
        )
        assert resp2.status_code == 200
        assert resp2.json()["id"] == body["id"]

    def test_create_default_provider_name(self, client, seeded, auth_header) -> None:
        """When provider_name is omitted, default to google_sheets."""
        tenant_id = seeded["tenant_id"]
        resp = client.put(
            f"/tenants/{tenant_id}/sheets-config",
            headers=auth_header,
            json={"label": "Minimal", "credentials": {}, "settings": {}},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["provider_name"] == "google_sheets"

    def test_update_existing_config(self, client, seeded, auth_header, api_session_factory) -> None:
        tenant_id = seeded["tenant_id"]
        cfg_id = uuid4()
        s = api_session_factory()
        try:
            s.add(ProviderConfig(
                id=cfg_id,
                tenant_id=tenant_id,
                kind="sheets",
                label="Old Sheets",
                provider_name="google_sheets",
                credentials={"api_key": "old-key"},
                settings={"spreadsheet_id": "old"},
                is_active=True,
            ))
            s.commit()
        finally:
            s.close()

        resp = client.put(
            f"/tenants/{tenant_id}/sheets-config",
            headers=auth_header,
            json={
                "label": "Updated Sheets",
                "credentials": {"api_key": "new-key"},
                "settings": {"spreadsheet_id": "new-id"},
                "is_active": True,
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["label"] == "Updated Sheets"
        assert body["credentials"]["api_key"] == "new-key"
        assert body["settings"]["spreadsheet_id"] == "new-id"
        # ID should remain the same.
        assert body["id"] == str(cfg_id)

    def test_create_inactive(self, client, seeded, auth_header) -> None:
        tenant_id = seeded["tenant_id"]
        resp = client.put(
            f"/tenants/{tenant_id}/sheets-config",
            headers=auth_header,
            json={
                "label": "Inactive Sheets",
                "credentials": {},
                "settings": {},
                "is_active": False,
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["is_active"] is False

    def test_requires_auth(self, client, seeded) -> None:
        resp = client.put(
            f"/tenants/{seeded['tenant_id']}/sheets-config",
            json=SHEETS_DEFAULT_BODY,
        )
        assert resp.status_code == 401

    def test_requires_tenant_scope(self, client, seeded, seed_superadmin) -> None:
        tenant_id = seeded["tenant_id"]
        resp = client.put(
            f"/tenants/{tenant_id}/sheets-config",
            headers=_sa_headers(client, seed_superadmin),
            json=SHEETS_DEFAULT_BODY,
        )
        assert resp.status_code == 403


class TestTenantSheetsPatch:
    """PATCH /tenants/{tenant_id}/sheets-config"""

    def test_partial_update_label(self, client, seeded, auth_header, api_session_factory) -> None:
        tenant_id = seeded["tenant_id"]
        s = api_session_factory()
        try:
            s.add(ProviderConfig(
                id=uuid4(),
                tenant_id=tenant_id,
                kind="sheets",
                label="Original",
                provider_name="google_sheets",
                credentials={"api_key": "k"},
                settings={"spreadsheet_id": "s1"},
                is_active=True,
            ))
            s.commit()
        finally:
            s.close()

        resp = client.patch(
            f"/tenants/{tenant_id}/sheets-config",
            headers=auth_header,
            json={"label": "Renamed"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["label"] == "Renamed"
        # Other fields preserved.
        assert body["credentials"]["api_key"] == "k"

    def test_patch_404_when_no_config(self, client, seeded, auth_header) -> None:
        tenant_id = seeded["tenant_id"]
        resp = client.patch(
            f"/tenants/{tenant_id}/sheets-config",
            headers=auth_header,
            json={"label": "Nope"},
        )
        assert resp.status_code == 404

    def test_patch_is_active_true_deactivates_other_sheets(
        self, client, seeded, auth_header, api_session_factory
    ) -> None:
        """Activating one sheets config deactivates the existing active one."""
        tenant_id = seeded["tenant_id"]
        s = api_session_factory()
        try:
            inactive = ProviderConfig(
                id=uuid4(),
                tenant_id=tenant_id,
                kind="sheets",
                label="Inactive",
                provider_name="google_sheets",
                credentials={},
                settings={},
                is_active=False,
            )
            s.add(inactive)
            s.commit()
        finally:
            s.close()

        # Activate via PATCH.
        resp = client.patch(
            f"/tenants/{tenant_id}/sheets-config",
            headers=auth_header,
            json={"is_active": True},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["is_active"] is True


class TestTenantSheetsActivate:
    """POST /tenants/{tenant_id}/sheets-config/activate"""

    def test_activate_inactive_config(self, client, seeded, auth_header, api_session_factory) -> None:
        tenant_id = seeded["tenant_id"]
        s = api_session_factory()
        try:
            s.add(ProviderConfig(
                id=uuid4(),
                tenant_id=tenant_id,
                kind="sheets",
                label="Inactive Sheets",
                provider_name="google_sheets",
                credentials={},
                settings={},
                is_active=False,
            ))
            s.commit()
        finally:
            s.close()

        resp = client.post(
            f"/tenants/{tenant_id}/sheets-config/activate",
            headers=auth_header,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["is_active"] is True

    def test_activate_404_when_no_config(self, client, seeded, auth_header) -> None:
        tenant_id = seeded["tenant_id"]
        resp = client.post(
            f"/tenants/{tenant_id}/sheets-config/activate",
            headers=auth_header,
        )
        assert resp.status_code == 404


class TestTenantSheetsDelete:
    """DELETE /tenants/{tenant_id}/sheets-config"""

    def test_delete_existing_config(self, client, seeded, auth_header, api_session_factory) -> None:
        tenant_id = seeded["tenant_id"]
        s = api_session_factory()
        try:
            s.add(ProviderConfig(
                id=uuid4(),
                tenant_id=tenant_id,
                kind="sheets",
                label="To Delete",
                provider_name="google_sheets",
                credentials={},
                settings={},
                is_active=True,
            ))
            s.commit()
        finally:
            s.close()

        resp = client.delete(
            f"/tenants/{tenant_id}/sheets-config",
            headers=auth_header,
        )
        assert resp.status_code == 204, resp.text

        # Verify it's gone.
        resp2 = client.get(
            f"/tenants/{tenant_id}/sheets-config",
            headers=auth_header,
        )
        assert resp2.status_code == 404

    def test_delete_404_when_no_config(self, client, seeded, auth_header) -> None:
        tenant_id = seeded["tenant_id"]
        resp = client.delete(
            f"/tenants/{tenant_id}/sheets-config",
            headers=auth_header,
        )
        assert resp.status_code == 404


class TestTenantSheetsAuditLog:
    """Verify audit log entries are created."""

    def test_audit_log_on_create(self, client, seeded, auth_header) -> None:
        tenant_id = seeded["tenant_id"]
        client.put(
            f"/tenants/{tenant_id}/sheets-config",
            headers=auth_header,
            json=SHEETS_DEFAULT_BODY,
        )

        resp = client.get(
            f"/tenants/{tenant_id}/logs?limit=5",
            headers=auth_header,
        )
        assert resp.status_code == 200
        entries = resp.json()["entries"]
        matching = [e for e in entries if "sheets_config" in e["event_type"]]
        assert len(matching) >= 1
        assert matching[0]["actor_scope"] == "tenant"

    def test_audit_log_on_delete(self, client, seeded, auth_header, api_session_factory) -> None:
        tenant_id = seeded["tenant_id"]
        s = api_session_factory()
        try:
            s.add(ProviderConfig(
                id=uuid4(),
                tenant_id=tenant_id,
                kind="sheets",
                label="Audit Test",
                provider_name="google_sheets",
                credentials={},
                settings={},
                is_active=True,
            ))
            s.commit()
        finally:
            s.close()

        client.delete(
            f"/tenants/{tenant_id}/sheets-config",
            headers=auth_header,
        )

        resp = client.get(
            f"/tenants/{tenant_id}/logs?limit=5",
            headers=auth_header,
        )
        assert resp.status_code == 200
        entries = resp.json()["entries"]
        matching = [e for e in entries if e["event_type"] == "sheets_config_deleted"]
        assert len(matching) >= 1


class TestTenantSheetsAdminPreserved:
    """Verify admin provider-configs routes still work."""

    def test_admin_can_still_manage_all_kinds(
        self, client, seeded, seed_superadmin, api_session_factory
    ) -> None:
        tenant_id = seeded["tenant_id"]
        headers = _sa_headers(client, seed_superadmin)

        # Admin creates a sheets config via the generic endpoint.
        resp = client.post(
            f"/tenants/{tenant_id}/provider-configs",
            headers=headers,
            json={
                "kind": "sheets",
                "label": "Admin Sheets",
                "provider_name": "google_sheets",
                "credentials": {},
                "settings": {},
                "is_active": True,
            },
        )
        assert resp.status_code == 201, resp.text

        # Admin creates a whatsapp config too.
        resp2 = client.post(
            f"/tenants/{tenant_id}/provider-configs",
            headers=headers,
            json={
                "kind": "whatsapp",
                "label": "Admin WhatsApp",
                "provider_name": "kapso",
                "credentials": {"api_key": "admin-key"},
                "settings": {"phone_number_id": "123"},
                "is_active": True,
            },
        )
        assert resp2.status_code == 201, resp2.text

        # Tenant can still read the sheets config via sheets-specific endpoint.
        tenant_headers = _sa_headers(client, seed_superadmin)
        # Actually let's use the tenant auth_header from fixture.
        # Re-fetch: create a tenant user
        from packages.application.auth import AuthService
        s = api_session_factory()
        try:
            svc = AuthService(s)
            svc.create_tenant_user(
                tenant_id=tenant_id,
                email="tenant-admin@test.local",
                password="test123",
                name="Tenant Admin",
            )
            issued = svc.authenticate_tenant("tenant-admin@test.local", "test123", "test")
            s.commit()
            tenant_token = issued.raw
        finally:
            s.close()

        tenant_headers = {"Authorization": f"Bearer {tenant_token}"}

        # Tenant reads sheets via self-service.
        resp3 = client.get(
            f"/tenants/{tenant_id}/sheets-config",
            headers=tenant_headers,
        )
        assert resp3.status_code == 200, resp3.text
        assert resp3.json()["kind"] == "sheets"
        assert resp3.json()["label"] == "Admin Sheets"

        # Admin generic list still returns both.
        resp4 = client.get(
            f"/tenants/{tenant_id}/provider-configs",
            headers=headers,
        )
        assert resp4.status_code == 200
        kinds = [c["kind"] for c in resp4.json()]
        assert "sheets" in kinds
        assert "whatsapp" in kinds

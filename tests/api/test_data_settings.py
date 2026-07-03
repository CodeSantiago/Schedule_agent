"""API tests for tenant data/integration policy (source of truth + sync mode).

Tests cover:
- Superadmin read/update data settings
- Tenant self-service read/update data settings
- Default values when no settings row exists
- Partial updates preserve other config sections
- Sheets connection state reporting
- Auth requirements
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from packages.infrastructure.db.models.providers import ProviderConfig
from packages.infrastructure.db.models.tenants import TenantSetting


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


# ── Tests: Superadmin ──────────────────────────────────────────────────────


class TestSuperadminDataSettings:
    """Superadmin reads/updates any tenant's data/integration settings."""

    def test_get_defaults_when_no_settings_row(self, client, seed_superadmin, seeded) -> None:
        """No tenant_settings row -> defaults (database, manual, not connected)."""
        headers = _sa_headers(client, seed_superadmin)
        tenant_id = seeded["tenant_id"]
        resp = client.get(
            f"/superadmin/tenants/{tenant_id}/settings/data",
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["source_of_truth"] == "database"
        assert body["sync_mode"] == "manual"
        assert body["sheets_connected"] is False

    def test_get_reflects_stored_config(self, client, seed_superadmin, seeded, api_session_factory) -> None:
        """Settings row with custom data config is returned correctly."""
        headers = _sa_headers(client, seed_superadmin)
        tenant_id = seeded["tenant_id"]
        s = api_session_factory()
        try:
            s.add(TenantSetting(tenant_id=tenant_id, config={
                "data": {"source_of_truth": "google_sheets", "sync_mode": "import_export"},
                "bot": {"enabled": True},
            }))
            s.commit()
        finally:
            s.close()
        resp = client.get(
            f"/superadmin/tenants/{tenant_id}/settings/data",
            headers=headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["source_of_truth"] == "google_sheets"
        assert body["sync_mode"] == "import_export"
        assert body["sheets_connected"] is False

    def test_update_sets_values_and_preserves_other_sections(
        self, client, seed_superadmin, seeded, api_session_factory
    ) -> None:
        """Updating data settings leaves unrelated config sections intact."""
        headers = _sa_headers(client, seed_superadmin)
        tenant_id = seeded["tenant_id"]

        # Pre-seed with bot config.
        s = api_session_factory()
        try:
            s.add(TenantSetting(tenant_id=tenant_id, config={
                "bot": {"enabled": True, "greeting_text": "Hi"},
                "booking": {"closed_dates": ["2026-12-25"]},
            }))
            s.commit()
        finally:
            s.close()

        # Update data settings.
        resp = client.put(
            f"/superadmin/tenants/{tenant_id}/settings/data",
            headers=headers,
            json={"source_of_truth": "hybrid", "sync_mode": "import_only"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["source_of_truth"] == "hybrid"
        assert body["sync_mode"] == "import_only"

        # Verify bot config preserved.
        bot_resp = client.get(
            f"/superadmin/tenants/{tenant_id}/settings/config",
            headers=headers,
        )
        assert bot_resp.status_code == 200
        bot_body = bot_resp.json()
        assert bot_body["greeting_text"] == "Hi"

        # Verify ops settings preserved.
        ops_resp = client.get(
            f"/superadmin/tenants/{tenant_id}/settings/operations",
            headers=headers,
        )
        assert ops_resp.status_code == 200
        ops_body = ops_resp.json()
        assert ops_body["bot_enabled"] is True
        assert ops_body["closed_dates"] == ["2026-12-25"]

    def test_partial_update_preserves_other_data_fields(
        self, client, seed_superadmin, seeded
    ) -> None:
        """Updating only source_of_truth leaves sync_mode unchanged."""
        headers = _sa_headers(client, seed_superadmin)
        tenant_id = seeded["tenant_id"]

        # First set both.
        client.put(
            f"/superadmin/tenants/{tenant_id}/settings/data",
            headers=headers,
            json={"source_of_truth": "hybrid", "sync_mode": "import_export"},
        )

        # Update only source_of_truth.
        resp = client.put(
            f"/superadmin/tenants/{tenant_id}/settings/data",
            headers=headers,
            json={"source_of_truth": "database"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["source_of_truth"] == "database"
        assert body["sync_mode"] == "import_export"

    def test_sheets_connected_true_when_active_config_exists(
        self, client, seed_superadmin, seeded, api_session_factory
    ) -> None:
        """sheets_connected is True when an active sheets provider config exists."""
        headers = _sa_headers(client, seed_superadmin)
        tenant_id = seeded["tenant_id"]

        # Create an active sheets provider config.
        s = api_session_factory()
        try:
            s.add(ProviderConfig(
                id=uuid4(),
                tenant_id=tenant_id,
                kind="sheets",
                label="Test Sheets",
                provider_name="google_sheets",
                credentials={},
                settings={},
                is_active=True,
            ))
            s.commit()
        finally:
            s.close()

        resp = client.get(
            f"/superadmin/tenants/{tenant_id}/settings/data",
            headers=headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["sheets_connected"] is True

    def test_sheets_connected_false_when_inactive_config_exists(
        self, client, seed_superadmin, seeded, api_session_factory
    ) -> None:
        """sheets_connected is False when only inactive sheets configs exist."""
        headers = _sa_headers(client, seed_superadmin)
        tenant_id = seeded["tenant_id"]

        s = api_session_factory()
        try:
            s.add(ProviderConfig(
                id=uuid4(),
                tenant_id=tenant_id,
                kind="sheets",
                label="Old Sheets",
                provider_name="google_sheets",
                credentials={},
                settings={},
                is_active=False,
            ))
            s.commit()
        finally:
            s.close()

        resp = client.get(
            f"/superadmin/tenants/{tenant_id}/settings/data",
            headers=headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["sheets_connected"] is False

    def test_requires_auth(self, client, seeded) -> None:
        """Without auth header the endpoint must 401."""
        resp = client.get(f"/superadmin/tenants/{seeded['tenant_id']}/settings/data")
        assert resp.status_code == 401

    def test_audit_log_written_on_update(
        self, client, seed_superadmin, seeded
    ) -> None:
        """Updating data settings creates an audit log entry."""
        headers = _sa_headers(client, seed_superadmin)
        tenant_id = seeded["tenant_id"]

        client.put(
            f"/superadmin/tenants/{tenant_id}/settings/data",
            headers=headers,
            json={"source_of_truth": "google_sheets"},
        )

        # Check audit log.
        resp = client.get(
            f"/superadmin/tenants/{tenant_id}/logs?limit=5",
            headers=headers,
        )
        assert resp.status_code == 200
        entries = resp.json()["entries"]
        matching = [e for e in entries if e["event_type"] == "data_settings_updated"]
        assert len(matching) >= 1
        assert "google_sheets" in str(matching[0])


# ── Tests: Tenant self-service ─────────────────────────────────────────────


class TestTenantDataSettings:
    """Tenant users read/update their own data/integration settings."""

    def test_get_defaults(self, client, seeded, auth_header) -> None:
        tenant_id = seeded["tenant_id"]
        resp = client.get(
            f"/tenants/{tenant_id}/settings/data",
            headers=auth_header,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["source_of_truth"] == "database"
        assert body["sync_mode"] == "manual"
        assert body["sheets_connected"] is False

    def test_update_and_read_back(self, client, seeded, auth_header) -> None:
        tenant_id = seeded["tenant_id"]
        resp = client.put(
            f"/tenants/{tenant_id}/settings/data",
            headers=auth_header,
            json={"source_of_truth": "google_sheets", "sync_mode": "import_only"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["source_of_truth"] == "google_sheets"
        assert body["sync_mode"] == "import_only"

        # Read back.
        resp = client.get(
            f"/tenants/{tenant_id}/settings/data",
            headers=auth_header,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["source_of_truth"] == "google_sheets"
        assert body["sync_mode"] == "import_only"

    def test_partial_update_preserves_other_fields(
        self, client, seeded, auth_header
    ) -> None:
        """Updating only sync_mode leaves source_of_truth unchanged."""
        tenant_id = seeded["tenant_id"]

        # First set both.
        client.put(
            f"/tenants/{tenant_id}/settings/data",
            headers=auth_header,
            json={"source_of_truth": "hybrid", "sync_mode": "import_export"},
        )

        # Update only sync_mode.
        resp = client.put(
            f"/tenants/{tenant_id}/settings/data",
            headers=auth_header,
            json={"sync_mode": "manual"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["source_of_truth"] == "hybrid"
        assert body["sync_mode"] == "manual"

    def test_requires_tenant_scope(self, client, seeded, seed_superadmin) -> None:
        """Superadmin token must NOT work on the tenant endpoint."""
        tenant_id = seeded["tenant_id"]
        headers = _sa_headers(client, seed_superadmin)
        resp = client.get(
            f"/tenants/{tenant_id}/settings/data",
            headers=headers,
        )
        assert resp.status_code == 403

    def test_requires_auth(self, client, seeded) -> None:
        resp = client.get(f"/tenants/{seeded['tenant_id']}/settings/data")
        assert resp.status_code == 401

    def test_audit_log_written_on_tenant_update(
        self, client, seeded, auth_header
    ) -> None:
        """Tenant updating data settings creates an audit log entry."""
        tenant_id = seeded["tenant_id"]

        client.put(
            f"/tenants/{tenant_id}/settings/data",
            headers=auth_header,
            json={"sync_mode": "import_export"},
        )

        # Check audit log.
        resp = client.get(
            f"/tenants/{tenant_id}/logs?limit=5",
            headers=auth_header,
        )
        assert resp.status_code == 200
        entries = resp.json()["entries"]
        matching = [e for e in entries if e["event_type"] == "data_settings_updated"]
        assert len(matching) >= 1

"""Tests for the customer identity settings endpoint.

Covers:
- GET defaults
- PUT update mode
- Invalid mode rejection
- Auth requirements
"""

from __future__ import annotations

from uuid import uuid4

import pytest


class TestIdentitySettingsAPI:
    """Test /tenants/{id}/settings/identity."""

    def test_get_defaults(self, client, auth_header, seeded):
        """No settings row -> default 'full_name'."""
        tenant_id = seeded["tenant_id"]
        resp = client.get(
            f"/tenants/{tenant_id}/settings/identity",
            headers=auth_header,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["mode"] == "full_name"

    def test_update_mode(self, client, auth_header, seeded, api_session_factory):
        """PUT changes the identity mode."""
        tenant_id = seeded["tenant_id"]
        resp = client.put(
            f"/tenants/{tenant_id}/settings/identity",
            headers=auth_header,
            json={"mode": "dni"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["mode"] == "dni"

        # Verify persistence
        resp2 = client.get(
            f"/tenants/{tenant_id}/settings/identity",
            headers=auth_header,
        )
        assert resp2.json()["mode"] == "dni"

    def test_all_valid_modes(self, client, auth_header, seeded):
        """All valid modes are accepted."""
        tenant_id = seeded["tenant_id"]
        for mode in ("full_name", "first_name_last_name", "dni", "full_name_dni", "first_name_last_name_dni"):
            resp = client.put(
                f"/tenants/{tenant_id}/settings/identity",
                headers=auth_header,
                json={"mode": mode},
            )
            assert resp.status_code == 200, f"mode={mode}: {resp.text}"

    def test_invalid_mode_rejected(self, client, auth_header, seeded):
        """Unknown mode -> 422."""
        tenant_id = seeded["tenant_id"]
        resp = client.put(
            f"/tenants/{tenant_id}/settings/identity",
            headers=auth_header,
            json={"mode": "invalid_mode_xyz"},
        )
        assert resp.status_code == 422

    def test_requires_auth(self, client, seeded):
        """No auth -> 401."""
        resp = client.get(f"/tenants/{seeded['tenant_id']}/settings/identity")
        assert resp.status_code == 401

    def test_requires_tenant_scope(self, client, seeded, superadmin_header):
        """Superadmin token -> 403."""
        resp = client.get(
            f"/tenants/{seeded['tenant_id']}/settings/identity",
            headers=superadmin_header,
        )
        assert resp.status_code == 403


class TestIdentityModeFormatting:
    """Test the identity mode formatting logic directly."""

    def test_full_name_mode(self):
        """full_name mode: returns customer_name as-is."""
        from packages.application.tenant.mode_service import IDENTITY_MODES

        # We test the format via the service — but we need a session + tenant.
        # Unit test the pure formatting logic below.
        pass

    def test_format_customer_name(self, api_session_factory, seeded):
        """format_customer_name produces correct display strings per mode."""
        from packages.application.tenant.mode_service import TenantModeService

        session = api_session_factory()
        try:
            svc = TenantModeService(session, seeded["tenant_id"])

            # Default (full_name)
            assert svc.format_customer_name("Juan", None, None) == "Juan"
            assert svc.format_customer_name("Juan", "Pérez", None) == "Juan"

            # first_name_last_name
            # Override mode via settings
            from packages.infrastructure.db.models.tenants import TenantSetting

            def _set_mode(mode: str):
                settings = session.query(TenantSetting).filter(
                    TenantSetting.tenant_id == seeded["tenant_id"]
                ).first()
                if settings is None:
                    session.add(TenantSetting(
                        tenant_id=seeded["tenant_id"],
                        config={"booking": {"customer_identity_mode": mode}},
                    ))
                else:
                    # Assign a completely new dict to avoid the identity-map
                    # weak-ref + shared-inner-dict bug where shallow-copy +
                    # reassign produces the same serialized JSON (no-op flush).
                    settings.config = {"booking": {"customer_identity_mode": mode}}
                session.flush()

            _set_mode("first_name_last_name")
            # Re-create service to pick up changes
            svc2 = TenantModeService(session, seeded["tenant_id"])
            assert svc2.format_customer_name("Juan", "Pérez", None) == "Juan Pérez"
            assert svc2.format_customer_name("Juan", None, None) == "Juan"

            _set_mode("dni")
            svc3 = TenantModeService(session, seeded["tenant_id"])
            assert svc3.format_customer_name("Juan", None, "12345678") == "12345678"
            assert svc3.format_customer_name(None, None, "87654321") == "87654321"

            _set_mode("full_name_dni")
            svc4 = TenantModeService(session, seeded["tenant_id"])
            assert svc4.format_customer_name("Juan Pérez", None, "12345678") == "Juan Pérez (12345678)"
            assert svc4.format_customer_name("Juan", None, None) == "Juan"

            _set_mode("first_name_last_name_dni")
            svc5 = TenantModeService(session, seeded["tenant_id"])
            assert svc5.format_customer_name("Juan", "Pérez", "12345678") == "Juan Pérez (12345678)"
        finally:
            session.close()

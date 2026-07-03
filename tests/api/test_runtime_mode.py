"""Tests for the runtime mode endpoint and tenant mode service.

Covers:
- Runtime mode GET returns correct defaults
- Runtime mode reflects config changes
- Mode service resolves sources correctly
- DB/Hybrid/Sheets modes
- Barber workspace today endpoint
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from packages.application.tenant.mode_service import (
    DATA_DEFAULTS,
    SOURCE_DATABASE,
    SOURCE_GOOGLE_SHEETS,
    SOURCE_HYBRID,
    VALID_SOURCES,
    TenantModeService,
)


# ── Tenant Mode Service unit tests ─────────────────────────────────────


class TestTenantModeService:
    """Unit tests for mode resolution (no HTTP, no sheets)."""

    def test_default_mode_when_no_settings(self, api_session_factory, seeded):
        """No TenantSetting row -> default 'database' mode."""
        session = api_session_factory()
        try:
            svc = TenantModeService(session, seeded["tenant_id"])
            assert svc.get_mode() == SOURCE_DATABASE
            assert svc.is_database_mode() is True
            assert svc.is_sheets_mode() is False
            assert svc.is_hybrid_mode() is False
        finally:
            session.close()

    def test_mode_reads_from_config(self, api_session_factory, seeded):
        """Mode reflects stored data.source_of_truth."""
        from packages.infrastructure.db.models.tenants import TenantSetting

        session = api_session_factory()
        try:
            session.add(TenantSetting(
                tenant_id=seeded["tenant_id"],
                config={"data": {"source_of_truth": SOURCE_GOOGLE_SHEETS}},
            ))
            session.commit()

            svc = TenantModeService(session, seeded["tenant_id"])
            assert svc.get_mode() == SOURCE_GOOGLE_SHEETS
            assert svc.is_database_mode() is False
            assert svc.is_sheets_mode() is True
        finally:
            session.close()

    def test_hybrid_mode(self, api_session_factory, seeded):
        """Hybrid mode resolves domain-specific sources."""
        from packages.infrastructure.db.models.tenants import TenantSetting

        session = api_session_factory()
        try:
            session.add(TenantSetting(
                tenant_id=seeded["tenant_id"],
                config={"data": {"source_of_truth": SOURCE_HYBRID}},
            ))
            session.commit()

            svc = TenantModeService(session, seeded["tenant_id"])
            assert svc.is_hybrid_mode() is True
            # Bot/barber_status -> sheets; everything else -> database
            assert svc.effective_source("bot") == SOURCE_GOOGLE_SHEETS
            assert svc.effective_source("barber_status") == SOURCE_GOOGLE_SHEETS
            assert svc.effective_source("scheduling") == SOURCE_DATABASE
            assert svc.effective_source("appointments") == SOURCE_DATABASE
        finally:
            session.close()

    def test_database_mode(self, api_session_factory, seeded):
        """Database mode: all domains resolve to database."""
        from packages.infrastructure.db.models.tenants import TenantSetting

        session = api_session_factory()
        try:
            session.add(TenantSetting(
                tenant_id=seeded["tenant_id"],
                config={"data": {"source_of_truth": SOURCE_DATABASE}},
            ))
            session.commit()

            svc = TenantModeService(session, seeded["tenant_id"])
            assert svc.get_bot_enabled() is True  # default
            for domain in ("bot", "barber_status", "scheduling", "appointments", "general"):
                assert svc.effective_source(domain) == SOURCE_DATABASE
        finally:
            session.close()

    def test_bot_enabled_db_fallback(self, api_session_factory, seeded):
        """Database mode reads bot_enabled from settings."""
        from packages.infrastructure.db.models.tenants import TenantSetting

        session = api_session_factory()
        try:
            session.add(TenantSetting(
                tenant_id=seeded["tenant_id"],
                config={
                    "data": {"source_of_truth": SOURCE_DATABASE},
                    "bot": {"enabled": False},
                },
            ))
            session.commit()

            svc = TenantModeService(session, seeded["tenant_id"])
            assert svc.get_bot_enabled() is False
        finally:
            session.close()


# ── API endpoint tests ─────────────────────────────────────────────────


class TestRuntimeModeAPI:
    """Test the GET /tenants/{id}/runtime/mode endpoint."""

    def test_defaults(self, client, auth_header, seeded):
        """No settings -> mode=database, sheets_connected=False."""
        tenant_id = seeded["tenant_id"]
        resp = client.get(
            f"/tenants/{tenant_id}/runtime/mode",
            headers=auth_header,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["mode"] == "database"
        assert body["sheets_connected"] is False
        assert body["domains"]["bot"] == "database"
        assert body["domains"]["scheduling"] == "database"
        assert body["constraints"]["sheets_write_back"] is False

    def test_reflects_google_sheets_mode(self, client, auth_header, seeded, api_session_factory):
        """Mode = google_sheets is reflected."""
        from packages.infrastructure.db.models.tenants import TenantSetting

        s = api_session_factory()
        try:
            s.add(TenantSetting(
                tenant_id=seeded["tenant_id"],
                config={"data": {"source_of_truth": "google_sheets"}},
            ))
            s.commit()
        finally:
            s.close()

        tenant_id = seeded["tenant_id"]
        resp = client.get(
            f"/tenants/{tenant_id}/runtime/mode",
            headers=auth_header,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["mode"] == "google_sheets"
        assert body["domains"]["bot"] == "google_sheets"
        assert body["domains"]["scheduling"] == "google_sheets"

    def test_reflects_hybrid_mode(self, client, auth_header, seeded, api_session_factory):
        """Hybrid mode shows domain-specific routing."""
        from packages.infrastructure.db.models.tenants import TenantSetting

        s = api_session_factory()
        try:
            s.add(TenantSetting(
                tenant_id=seeded["tenant_id"],
                config={"data": {"source_of_truth": "hybrid"}},
            ))
            s.commit()
        finally:
            s.close()

        tenant_id = seeded["tenant_id"]
        resp = client.get(
            f"/tenants/{tenant_id}/runtime/mode",
            headers=auth_header,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["mode"] == "hybrid"
        assert body["domains"]["bot"] == "google_sheets"
        assert body["domains"]["barber_status"] == "google_sheets"
        assert body["domains"]["scheduling"] == "database"
        assert body["domains"]["appointments"] == "database"

    def test_requires_auth(self, client, seeded):
        """No auth -> 401."""
        resp = client.get(f"/tenants/{seeded['tenant_id']}/runtime/mode")
        assert resp.status_code == 401

    def test_requires_tenant_scope(self, client, seeded, superadmin_header):
        """Superadmin token -> 403."""
        resp = client.get(
            f"/tenants/{seeded['tenant_id']}/runtime/mode",
            headers=superadmin_header,
        )
        assert resp.status_code == 403

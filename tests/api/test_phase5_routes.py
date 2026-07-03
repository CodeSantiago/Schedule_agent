"""API tests for Phase 5 features: health, feature flags, export/import,
draft/publish, tenant users, and calendar overrides.

Reuses the same ``seeded`` + ``auth_header`` + ``tenant_token`` fixture
pattern from ``conftest.py``.
"""

from __future__ import annotations

from uuid import uuid4


# =========================================================================
# Health / Status Center
# =========================================================================


class TestHealthRoute:
    def test_get_health_ok(self, client, seeded, auth_header) -> None:
        resp = client.get(
            f"/tenants/{seeded['tenant_id']}/health",
            headers=auth_header,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["tenant_id"] == str(seeded["tenant_id"])
        assert "bot" in body
        assert "sheets" in body
        assert "data_policy" in body
        assert "counts" in body
        assert "overall" in body
        assert body["overall"] in ("healthy", "attention", "critical")

    def test_get_health_401(self, client, seeded) -> None:
        resp = client.get(f"/tenants/{seeded['tenant_id']}/health")
        assert resp.status_code == 401

    def test_get_health_403_wrong_tenant(
        self, client, seeded, auth_header
    ) -> None:
        resp = client.get(
            f"/tenants/{uuid4()}/health",
            headers=auth_header,
        )
        assert resp.status_code == 403

    def test_superadmin_get_health(
        self, client, seeded, superadmin_header
    ) -> None:
        resp = client.get(
            f"/superadmin/tenants/{seeded['tenant_id']}/health",
            headers=superadmin_header,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["overall"] in ("healthy", "attention", "critical")

    def test_superadmin_get_health_401(self, client, seeded) -> None:
        resp = client.get(
            f"/superadmin/tenants/{seeded['tenant_id']}/health"
        )
        assert resp.status_code == 401


# =========================================================================
# Feature Flags
# =========================================================================


class TestFeatureFlagsRoute:
    def test_get_features(self, client, seeded, auth_header) -> None:
        resp = client.get(
            f"/tenants/{seeded['tenant_id']}/settings/features",
            headers=auth_header,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "features" in body
        assert "available_flags" in body
        assert "cb_booking" in body["features"]

    def test_update_features(self, client, seeded, auth_header) -> None:
        resp = client.put(
            f"/tenants/{seeded['tenant_id']}/settings/features",
            json={"features": {"cb_booking": False, "reports": True}},
            headers=auth_header,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["features"]["cb_booking"] is False
        assert resp.json()["features"]["reports"] is True

    def test_update_features_401(self, client, seeded) -> None:
        resp = client.put(
            f"/tenants/{seeded['tenant_id']}/settings/features",
            json={"features": {"cb_booking": False}},
        )
        assert resp.status_code == 401

    def test_superadmin_get_features(
        self, client, seeded, superadmin_header
    ) -> None:
        resp = client.get(
            f"/superadmin/tenants/{seeded['tenant_id']}/settings/features",
            headers=superadmin_header,
        )
        assert resp.status_code == 200, resp.text

    def test_superadmin_update_features(
        self, client, seeded, superadmin_header
    ) -> None:
        resp = client.put(
            f"/superadmin/tenants/{seeded['tenant_id']}/settings/features",
            json={"features": {"online_payments": True}},
            headers=superadmin_header,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["features"]["online_payments"] is True


# =========================================================================
# Tenant Users (RBAC) — tenant self-service
# =========================================================================


class TestTenantUsersRoute:
    def test_list_users(self, client, seeded, auth_header) -> None:
        resp = client.get(
            f"/tenants/{seeded['tenant_id']}/users",
            headers=auth_header,
        )
        assert resp.status_code == 200, resp.text
        users = resp.json()
        assert isinstance(users, list)
        # The seeded tenant has one user (created by tenant_token fixture).

    def test_create_user(self, client, seeded, auth_header) -> None:
        resp = client.post(
            f"/tenants/{seeded['tenant_id']}/users",
            json={
                "email": "staff@test.local",
                "password": "test123",
                "name": "Staff User",
                "role": "staff",
            },
            headers=auth_header,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["email"] == "staff@test.local"
        assert body["role"] == "staff"
        assert body["is_active"] is True

    def test_create_user_invalid_role(
        self, client, seeded, auth_header
    ) -> None:
        resp = client.post(
            f"/tenants/{seeded['tenant_id']}/users",
            json={
                "email": "bad@test.local",
                "password": "test123",
                "name": "Bad",
                "role": "superadmin",
            },
            headers=auth_header,
        )
        assert resp.status_code == 400

    def test_list_users_401(self, client, seeded) -> None:
        resp = client.get(f"/tenants/{seeded['tenant_id']}/users")
        assert resp.status_code == 401

    def test_superadmin_list_users(
        self, client, seeded, superadmin_header
    ) -> None:
        resp = client.get(
            f"/superadmin/tenants/{seeded['tenant_id']}/users",
            headers=superadmin_header,
        )
        assert resp.status_code == 200, resp.text

    def test_superadmin_create_user(
        self, client, seeded, superadmin_header
    ) -> None:
        resp = client.post(
            f"/superadmin/tenants/{seeded['tenant_id']}/users",
            json={
                "email": "super-created@test.local",
                "password": "test123",
                "name": "Super Created",
                "role": "admin",
            },
            headers=superadmin_header,
        )
        assert resp.status_code == 201, resp.text


# =========================================================================
# Calendar Overrides
# =========================================================================


class TestCalendarOverridesRoute:
    def test_get_calendar(self, client, seeded, auth_header) -> None:
        resp = client.get(
            f"/tenants/{seeded['tenant_id']}/settings/operations/calendar",
            headers=auth_header,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "holiday_ranges" in body
        assert "barber_overrides" in body
        assert "closed_dates" in body

    def test_update_calendar_holiday(
        self, client, seeded, auth_header
    ) -> None:
        resp = client.put(
            f"/tenants/{seeded['tenant_id']}/settings/operations/calendar",
            json={
                "holiday_ranges": [
                    {
                        "start": "2026-12-24",
                        "end": "2026-12-26",
                        "reason": "Christmas",
                    }
                ],
                "barber_overrides": [],
            },
            headers=auth_header,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["holiday_ranges"]) == 1
        assert body["holiday_ranges"][0]["reason"] == "Christmas"

    def test_update_calendar_401(self, client, seeded) -> None:
        resp = client.put(
            f"/tenants/{seeded['tenant_id']}/settings/operations/calendar",
            json={"holiday_ranges": []},
        )
        assert resp.status_code == 401


# =========================================================================
# Draft / Publish
# =========================================================================


class TestDraftPublishRoute:
    def test_get_draft_status(self, client, seeded, auth_header) -> None:
        resp = client.get(
            f"/tenants/{seeded['tenant_id']}/settings/draft",
            headers=auth_header,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "sections" in body

    def test_save_and_publish_draft(
        self, client, seeded, auth_header
    ) -> None:
        tid = seeded["tenant_id"]

        # Save a draft for bot section.
        resp = client.put(
            f"/tenants/{tid}/settings/draft/bot",
            json={"greeting_text": "Draft greeting", "enabled": True},
            headers=auth_header,
        )
        assert resp.status_code == 200, resp.text

        # Verify draft status shows it.
        resp = client.get(
            f"/tenants/{tid}/settings/draft",
            headers=auth_header,
        )
        assert resp.status_code == 200
        assert resp.json()["sections"]["bot"]["has_draft"] is True

        # Publish the draft.
        resp = client.post(
            f"/tenants/{tid}/settings/draft/publish/bot",
            headers=auth_header,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True
        assert body["section"] == "bot"

        # After publish, draft should be gone.
        resp = client.get(
            f"/tenants/{tid}/settings/draft",
            headers=auth_header,
        )
        assert resp.status_code == 200
        assert resp.json()["sections"]["bot"]["has_draft"] is False

    def test_discard_draft(self, client, seeded, auth_header) -> None:
        tid = seeded["tenant_id"]

        # Save a draft.
        client.put(
            f"/tenants/{tid}/settings/draft/booking",
            json={"closed_dates": ["2026-12-25"]},
            headers=auth_header,
        )

        # Discard it.
        resp = client.delete(
            f"/tenants/{tid}/settings/draft/booking",
            headers=auth_header,
        )
        assert resp.status_code == 200, resp.text

        # Verify draft is gone.
        resp = client.get(
            f"/tenants/{tid}/settings/draft",
            headers=auth_header,
        )
        assert resp.json()["sections"]["booking"]["has_draft"] is False

    def test_draft_401(self, client, seeded) -> None:
        resp = client.get(
            f"/tenants/{seeded['tenant_id']}/settings/draft",
        )
        assert resp.status_code == 401


# =========================================================================
# Export / Import
# =========================================================================


class TestExportImportRoute:
    def test_export_tenant(self, client, seeded, auth_header) -> None:
        resp = client.get(
            f"/tenants/{seeded['tenant_id']}/export",
            headers=auth_header,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "barbers" in body
        assert "services" in body
        assert "export_version" in body
        assert len(body["barbers"]) > 0

    def test_export_401(self, client, seeded) -> None:
        resp = client.get(f"/tenants/{seeded['tenant_id']}/export")
        assert resp.status_code == 401

    def test_import_tenant(self, client, seeded, auth_header) -> None:
        # First export.
        export_resp = client.get(
            f"/tenants/{seeded['tenant_id']}/export",
            headers=auth_header,
        )
        assert export_resp.status_code == 200
        data = export_resp.json()

        # Then import back.
        resp = client.post(
            f"/tenants/{seeded['tenant_id']}/import",
            json=data,
            headers=auth_header,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True


# =========================================================================
# Onboarding Templates (superadmin only)
# =========================================================================


class TestTemplatesRoute:
    def test_list_templates(self, client, seeded, superadmin_header) -> None:
        resp = client.get(
            "/superadmin/templates",
            headers=superadmin_header,
        )
        assert resp.status_code == 200, resp.text
        templates = resp.json()
        assert len(templates) >= 2
        assert templates[0]["id"] is not None

    def test_get_template(self, client, seeded, superadmin_header) -> None:
        resp = client.get(
            "/superadmin/templates/barberia-clasica",
            headers=superadmin_header,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["name"] == "Barbería Clásica"

    def test_apply_template(
        self, client, seeded, superadmin_header
    ) -> None:
        resp = client.post(
            f"/superadmin/templates/barberia-clasica/apply/{seeded['tenant_id']}",
            headers=superadmin_header,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True
        assert body["template"] == "barberia-clasica"

    def test_templates_401(self, client, seeded) -> None:
        resp = client.get("/superadmin/templates")
        assert resp.status_code == 401

    def test_template_404(self, client, seeded, superadmin_header) -> None:
        resp = client.get(
            "/superadmin/templates/nonexistent",
            headers=superadmin_header,
        )
        assert resp.status_code == 404

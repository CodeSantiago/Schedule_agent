"""API tests for tenant operational settings (bot toggle + closed dates).

Tests cover:
- Superadmin read/update tenant operational settings
- Tenant self-service read/update operational settings
- Booking rejection on closed dates
- Bot pause when disabled
"""

from __future__ import annotations

from uuid import uuid4

import pytest

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


def _kapso_payload(msg_id: str, from_phone: str, body: str) -> dict:
    """Build a Kapso-native webhook payload to pass _extract_message."""
    return {
        "message": {
            "id": msg_id,
            "from": from_phone,
            "text": {"body": body},
            "type": "text",
        },
        "conversation": {},
        "is_new_conversation": False,
        "phone_number_id": "12345",
    }


# ── Tests ──────────────────────────────────────────────────────────────────


class TestSuperadminOperationalSettings:
    """Superadmin reads/updates any tenant's operational settings."""

    def test_get_defaults_when_no_settings_row(self, client, seed_superadmin, seeded) -> None:
        """No tenant_settings row -> defaults (bot on, no closed dates)."""
        headers = _sa_headers(client, seed_superadmin)
        tenant_id = seeded["tenant_id"]
        resp = client.get(
            f"/superadmin/tenants/{tenant_id}/settings/operations",
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["bot_enabled"] is True
        assert body["closed_dates"] == []

    def test_get_reflects_stored_config(self, client, seed_superadmin, seeded, api_session_factory) -> None:
        """Settings row with custom config is returned correctly."""
        headers = _sa_headers(client, seed_superadmin)
        tenant_id = seeded["tenant_id"]
        s = api_session_factory()
        try:
            s.add(TenantSetting(tenant_id=tenant_id, config={
                "bot": {"enabled": False},
                "booking": {"closed_dates": ["2026-12-25"]},
            }))
            s.commit()
        finally:
            s.close()
        resp = client.get(
            f"/superadmin/tenants/{tenant_id}/settings/operations",
            headers=headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["bot_enabled"] is False
        assert body["closed_dates"] == ["2026-12-25"]

    def test_update_bot_only(self, client, seed_superadmin, seeded) -> None:
        """Updating only bot_enabled leaves closed_dates unchanged."""
        headers = _sa_headers(client, seed_superadmin)
        tenant_id = seeded["tenant_id"]
        # First set both fields.
        client.put(
            f"/superadmin/tenants/{tenant_id}/settings/operations",
            headers=headers,
            json={"bot_enabled": False, "closed_dates": ["2026-07-04"]},
        )
        # Now update only bot_enabled.
        resp = client.put(
            f"/superadmin/tenants/{tenant_id}/settings/operations",
            headers=headers,
            json={"bot_enabled": True},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["bot_enabled"] is True
        assert body["closed_dates"] == ["2026-07-04"]

    def test_requires_auth(self, client, seeded) -> None:
        """Without auth header the endpoint must 401."""
        resp = client.get(f"/superadmin/tenants/{seeded['tenant_id']}/settings/operations")
        assert resp.status_code == 401


class TestTenantOperationalSettings:
    """Tenant users read/update their own operational settings."""

    def test_get_defaults(self, client, seeded, auth_header) -> None:
        tenant_id = seeded["tenant_id"]
        resp = client.get(
            f"/tenants/{tenant_id}/settings/operations",
            headers=auth_header,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["bot_enabled"] is True
        assert body["closed_dates"] == []

    def test_update_and_read_back(self, client, seeded, auth_header) -> None:
        tenant_id = seeded["tenant_id"]
        resp = client.put(
            f"/tenants/{tenant_id}/settings/operations",
            headers=auth_header,
            json={"bot_enabled": False, "closed_dates": ["2026-12-25", "2027-01-01"]},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["bot_enabled"] is False
        assert set(body["closed_dates"]) == {"2026-12-25", "2027-01-01"}

        # Read back.
        resp = client.get(
            f"/tenants/{tenant_id}/settings/operations",
            headers=auth_header,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["bot_enabled"] is False
        assert set(body["closed_dates"]) == {"2026-12-25", "2027-01-01"}

    def test_requires_tenant_scope(self, client, seeded, seed_superadmin) -> None:
        """Superadmin token must NOT work on the tenant endpoint."""
        tenant_id = seeded["tenant_id"]
        headers = _sa_headers(client, seed_superadmin)
        resp = client.get(
            f"/tenants/{tenant_id}/settings/operations",
            headers=headers,
        )
        assert resp.status_code == 403  # superadmin scope not allowed on tenant endpoint

    def test_requires_auth(self, client, seeded) -> None:
        resp = client.get(f"/tenants/{seeded['tenant_id']}/settings/operations")
        assert resp.status_code == 401


class TestBookingClosedDate:
    """Booking on a closed date is rejected."""

    def test_book_on_closed_date_returns_422(self, client, seeded, auth_header, api_session_factory) -> None:
        tenant_id = seeded["tenant_id"]
        s = api_session_factory()
        try:
            s.add(TenantSetting(tenant_id=tenant_id, config={
                "bot": {"enabled": True},
                "booking": {"closed_dates": ["2026-07-01"]},
            }))
            s.commit()
        finally:
            s.close()

        resp = client.post(
            f"/tenants/{tenant_id}/appointments",
            headers=auth_header,
            json={
                "barber_id": str(seeded["barber_id"]),
                "service_id": str(seeded["haircut_service_id"]),
                "start_at": "2026-07-01T10:00:00",
                "customer_name": "Test",
                "customer_phone": "+5491100000001",
            },
        )
        assert resp.status_code == 422, resp.text
        assert "closed" in resp.json()["detail"].lower()

    def test_book_on_open_date_still_works(self, client, seeded, auth_header, api_session_factory) -> None:
        tenant_id = seeded["tenant_id"]
        # Close some dates but leave our target open.
        s = api_session_factory()
        try:
            s.add(TenantSetting(tenant_id=tenant_id, config={
                "bot": {"enabled": True},
                "booking": {"closed_dates": ["2026-12-25"]},
            }))
            s.commit()
        finally:
            s.close()

        # 2026-06-24 is a Wednesday, the seeded barber has schedule 10-20.
        resp = client.post(
            f"/tenants/{tenant_id}/appointments",
            headers=auth_header,
            json={
                "barber_id": str(seeded["barber_id"]),
                "service_id": str(seeded["haircut_service_id"]),
                "start_at": "2026-06-24T10:00:00",
                "customer_name": "Test",
                "customer_phone": "+5491100000002",
            },
        )
        assert resp.status_code == 201, resp.text


class TestBotDisabled:
    """Webhook replies with pause message when bot is disabled."""

    def test_bot_disabled_returns_pause_message(self, client, seeded, api_session_factory) -> None:
        """When bot is disabled, the webhook should return a pause reply."""
        tenant_id = seeded["tenant_id"]
        s = api_session_factory()
        try:
            s.add(TenantSetting(tenant_id=tenant_id, config={
                "bot": {"enabled": False},
                "booking": {"closed_dates": []},
            }))
            s.commit()
        finally:
            s.close()

        resp = client.post(
            f"/webhooks/whatsapp/{tenant_id}",
            json=_kapso_payload("wamid.botoff", "+5491100000999", "hola"),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["accepted"] is True
        assert body["state"] == "bot-paused"
        assert "paused" in body["reply"].lower()

    def test_bot_enabled_processes_normally(self, client, seeded, api_session_factory) -> None:
        """When bot is enabled, the first message should get the greeting."""
        tenant_id = seeded["tenant_id"]
        s = api_session_factory()
        try:
            s.add(TenantSetting(tenant_id=tenant_id, config={
                "bot": {"enabled": True},
                "booking": {"closed_dates": []},
            }))
            s.commit()
        finally:
            s.close()

        resp = client.post(
            f"/webhooks/whatsapp/{tenant_id}",
            json=_kapso_payload("wamid.boton", "+5491100000888", "hola"),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["accepted"] is True
        assert body["state"] == "awaiting_menu"
        assert "Hola" in body["reply"]

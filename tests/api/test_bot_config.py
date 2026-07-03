"""API tests for tenant bot + business config (greeting, behavior, display).

Tests cover:
- Superadmin read/update bot/business config
- Tenant self-service read/update bot/business config
- Default values when no settings row exists
- Partial updates preserve other fields
- Auth requirements
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


# ── Tests: Superadmin ──────────────────────────────────────────────────────


class TestSuperadminBotConfig:
    """Superadmin reads/updates any tenant's bot + business config."""

    def test_get_defaults_when_no_settings_row(
        self, client, seed_superadmin, seeded
    ) -> None:
        """No tenant_settings row -> default empty strings."""
        headers = _sa_headers(client, seed_superadmin)
        tenant_id = seeded["tenant_id"]
        resp = client.get(
            f"/superadmin/tenants/{tenant_id}/settings/config",
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["greeting_text"] == ""
        assert body["behavior_notes"] == ""
        assert body["display_name"] == ""
        assert body["contact_phone"] == ""
        assert body["booking_notes"] == ""

    def test_get_reflects_stored_config(
        self, client, seed_superadmin, seeded, api_session_factory
    ) -> None:
        """Settings row with custom config is returned correctly."""
        headers = _sa_headers(client, seed_superadmin)
        tenant_id = seeded["tenant_id"]
        s = api_session_factory()
        try:
            s.add(TenantSetting(tenant_id=tenant_id, config={
                "bot": {"enabled": True, "greeting_text": "Custom hi", "behavior_notes": "Be friendly"},
                "business": {"display_name": "Test Shop", "contact_phone": "+123", "booking_notes": "Bring cash"},
            }))
            s.commit()
        finally:
            s.close()
        resp = client.get(
            f"/superadmin/tenants/{tenant_id}/settings/config",
            headers=headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["greeting_text"] == "Custom hi"
        assert body["behavior_notes"] == "Be friendly"
        assert body["display_name"] == "Test Shop"
        assert body["contact_phone"] == "+123"
        assert body["booking_notes"] == "Bring cash"

    def test_partial_update_does_not_clear_other_fields(
        self, client, seed_superadmin, seeded
    ) -> None:
        """Updating only greeting_text leaves other fields unchanged."""
        headers = _sa_headers(client, seed_superadmin)
        tenant_id = seeded["tenant_id"]
        # First set all fields.
        client.put(
            f"/superadmin/tenants/{tenant_id}/settings/config",
            headers=headers,
            json={
                "greeting_text": "Hello",
                "behavior_notes": "Notes",
                "display_name": "Shop",
                "contact_phone": "+1",
                "booking_notes": "Cash only",
            },
        )
        # Now update only one field.
        resp = client.put(
            f"/superadmin/tenants/{tenant_id}/settings/config",
            headers=headers,
            json={"greeting_text": "Updated greeting"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["greeting_text"] == "Updated greeting"
        assert body["behavior_notes"] == "Notes"
        assert body["display_name"] == "Shop"
        assert body["contact_phone"] == "+1"
        assert body["booking_notes"] == "Cash only"

    def test_does_not_trample_ops_keys(
        self, client, seed_superadmin, seeded, api_session_factory
    ) -> None:
        """Writing bot config preserves bot.enabled and booking.closed_dates."""
        headers = _sa_headers(client, seed_superadmin)
        tenant_id = seeded["tenant_id"]
        # Pre-seed ops settings.
        s = api_session_factory()
        try:
            s.add(TenantSetting(tenant_id=tenant_id, config={
                "bot": {"enabled": False},
                "booking": {"closed_dates": ["2026-12-25"]},
            }))
            s.commit()
        finally:
            s.close()

        # Write bot config.
        client.put(
            f"/superadmin/tenants/{tenant_id}/settings/config",
            headers=headers,
            json={"greeting_text": "Hola!"},
        )

        # Ops should still reflect original values.
        ops_resp = client.get(
            f"/superadmin/tenants/{tenant_id}/settings/operations",
            headers=headers,
        )
        assert ops_resp.status_code == 200
        ops_body = ops_resp.json()
        assert ops_body["bot_enabled"] is False
        assert ops_body["closed_dates"] == ["2026-12-25"]

    def test_requires_auth(self, client, seeded) -> None:
        """Without auth header the endpoint must 401."""
        resp = client.get(f"/superadmin/tenants/{seeded['tenant_id']}/settings/config")
        assert resp.status_code == 401


# ── Tests: Tenant self-service ─────────────────────────────────────────────


class TestTenantBotConfig:
    """Tenant users read/update their own bot + business config."""

    def test_get_defaults(self, client, seeded, auth_header) -> None:
        tenant_id = seeded["tenant_id"]
        resp = client.get(
            f"/tenants/{tenant_id}/settings/config",
            headers=auth_header,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["greeting_text"] == ""
        assert body["behavior_notes"] == ""

    def test_update_and_read_back(self, client, seeded, auth_header) -> None:
        tenant_id = seeded["tenant_id"]
        resp = client.put(
            f"/tenants/{tenant_id}/settings/config",
            headers=auth_header,
            json={
                "greeting_text": "Welcome!",
                "behavior_notes": "Be polite",
                "display_name": "My Shop",
                "contact_phone": "+54911",
                "booking_notes": "Cash or transfer",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["greeting_text"] == "Welcome!"
        assert body["behavior_notes"] == "Be polite"
        assert body["display_name"] == "My Shop"
        assert body["contact_phone"] == "+54911"
        assert body["booking_notes"] == "Cash or transfer"

        # Read back.
        resp = client.get(
            f"/tenants/{tenant_id}/settings/config",
            headers=auth_header,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["greeting_text"] == "Welcome!"

    def test_requires_tenant_scope(
        self, client, seeded, seed_superadmin
    ) -> None:
        """Superadmin token must NOT work on the tenant endpoint."""
        tenant_id = seeded["tenant_id"]
        headers = _sa_headers(client, seed_superadmin)
        resp = client.get(
            f"/tenants/{tenant_id}/settings/config",
            headers=headers,
        )
        assert resp.status_code == 403

    def test_requires_auth(self, client, seeded) -> None:
        resp = client.get(f"/tenants/{seeded['tenant_id']}/settings/config")
        assert resp.status_code == 401


# ── Tests: Greeting override in webhook ────────────────────────────────────


class TestGreetingOverride:
    """Webhook uses configured greeting when present."""

    def _kapso_payload(self, msg_id: str, from_phone: str, body: str) -> dict:
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

    def test_custom_greeting_used_when_set(
        self, client, seeded, api_session_factory
    ) -> None:
        """When bot.greeting_text is set, the webhook should use it."""
        tenant_id = seeded["tenant_id"]
        s = api_session_factory()
        try:
            s.add(TenantSetting(tenant_id=tenant_id, config={
                "bot": {
                    "enabled": True,
                    "greeting_text": "Bienvenido a mi barberia! Como puedo ayudarte?",
                },
                "booking": {"closed_dates": []},
            }))
            s.commit()
        finally:
            s.close()

        resp = client.post(
            f"/webhooks/whatsapp/{tenant_id}",
            json=self._kapso_payload("wamid.greet1", "+5491100000111", "hola"),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["accepted"] is True
        assert "Bienvenido a mi barberia" in body["reply"]
        assert body["state"] == "awaiting_menu"

    def test_default_greeting_when_empty(
        self, client, seeded, api_session_factory
    ) -> None:
        """When greeting_text is empty string, the hardcoded default is used."""
        tenant_id = seeded["tenant_id"]
        s = api_session_factory()
        try:
            s.add(TenantSetting(tenant_id=tenant_id, config={
                "bot": {
                    "enabled": True,
                    "greeting_text": "",
                },
                "booking": {"closed_dates": []},
            }))
            s.commit()
        finally:
            s.close()

        resp = client.post(
            f"/webhooks/whatsapp/{tenant_id}",
            json=self._kapso_payload("wamid.greet2", "+5491100000222", "hola"),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["accepted"] is True
        # The default greeting starts with "Hola"
        assert "Hola" in body["reply"]
        assert body["state"] == "awaiting_menu"

    def test_default_greeting_when_no_settings(
        self, client, seeded
    ) -> None:
        """When no settings row exists, the hardcoded default is used."""
        tenant_id = seeded["tenant_id"]
        # No TenantSetting row at all.
        resp = client.post(
            f"/webhooks/whatsapp/{tenant_id}",
            json=self._kapso_payload("wamid.greet3", "+5491100000333", "hola"),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["accepted"] is True
        assert "Hola" in body["reply"]
        assert body["state"] == "awaiting_menu"

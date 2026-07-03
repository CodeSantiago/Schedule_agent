"""API tests for the audit / operational log endpoints.

Covers:
- Tenant reads own logs (empty and with entries)
- Superadmin reads any tenant's logs
- Cross-tenant isolation (tenant A cannot see tenant B's logs)
- Log write via settings update (both tenant and superadmin)
- Log write via bot-paused webhook
- Log write via booking closed-date rejection
- Event type filtering
- Auth required
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from packages.infrastructure.db.models.audit_log import TenantAuditLog


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def seed_superadmin(api_session_factory):
    from packages.application.auth import AuthService

    s = api_session_factory()
    try:
        svc = AuthService(s)
        svc.create_superadmin("audit-owner@barber.io", "topsecret-pw")
        s.commit()
    finally:
        s.close()
    return {"email": "audit-owner@barber.io", "password": "topsecret-pw"}


def _sa_headers(client, seed_superadmin) -> dict:
    resp = client.post(
        "/superadmin/auth/login",
        json={"email": seed_superadmin["email"], "password": seed_superadmin["password"]},
    )
    return {"Authorization": f"Bearer {resp.json()['token']}"}


def _seed_logs(session, tenant_id: UUID, count: int = 3) -> list[TenantAuditLog]:
    """Insert `count` log entries for the given tenant and return them."""
    entries = []
    for i in range(count):
        e = TenantAuditLog(
            id=uuid4(),
            tenant_id=tenant_id,
            event_type="test_event",
            level="info",
            message=f"Test log entry {i}",
            details={"index": i},
        )
        session.add(e)
        entries.append(e)
    session.commit()
    return entries


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestTenantAuditLogs:
    """Tenant-scoped log visibility."""

    def test_empty_logs(self, client, seeded, auth_header) -> None:
        tenant_id = seeded["tenant_id"]
        resp = client.get(
            f"/tenants/{tenant_id}/logs",
            headers=auth_header,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["entries"] == []

    def test_logs_visible(self, client, seeded, auth_header, api_session_factory) -> None:
        tenant_id = seeded["tenant_id"]
        s = api_session_factory()
        try:
            _seed_logs(s, tenant_id, count=3)
        finally:
            s.close()

        resp = client.get(
            f"/tenants/{tenant_id}/logs",
            headers=auth_header,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["entries"]) == 3

    def test_logs_require_auth(self, client, seeded) -> None:
        resp = client.get(f"/tenants/{seeded['tenant_id']}/logs")
        assert resp.status_code == 401

    def test_cross_tenant_isolation(self, client, seeded, auth_header, api_session_factory) -> None:
        """Tenant A cannot see Tenant B's logs."""
        tenant_a = seeded["tenant_id"]
        tenant_b = uuid4()

        s = api_session_factory()
        try:
            _seed_logs(s, tenant_b, count=2)
        finally:
            s.close()

        resp = client.get(
            f"/tenants/{tenant_a}/logs",
            headers=auth_header,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Logs for tenant_b should not be visible to tenant_a.
        assert len(body["entries"]) == 0

    def test_filter_by_event_type(self, client, seeded, auth_header, api_session_factory) -> None:
        tenant_id = seeded["tenant_id"]
        s = api_session_factory()
        try:
            _seed_logs(s, tenant_id, count=2)
            extra = TenantAuditLog(
                id=uuid4(),
                tenant_id=tenant_id,
                event_type="settings_updated",
                level="info",
                message="Settings changed",
            )
            s.add(extra)
            s.commit()
        finally:
            s.close()

        resp = client.get(
            f"/tenants/{tenant_id}/logs?event_type=settings_updated",
            headers=auth_header,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["entries"]) == 1
        assert body["entries"][0]["event_type"] == "settings_updated"


class TestSuperadminAuditLogs:
    """Superadmin log visibility."""

    def test_superadmin_can_read_any_tenant_logs(
        self, client, seeded, seed_superadmin, api_session_factory
    ) -> None:
        tenant_id = seeded["tenant_id"]
        s = api_session_factory()
        try:
            _seed_logs(s, tenant_id, count=2)
        finally:
            s.close()

        headers = _sa_headers(client, seed_superadmin)
        resp = client.get(
            f"/superadmin/tenants/{tenant_id}/logs",
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["entries"]) == 2

    def test_superadmin_requires_auth(self, client, seeded) -> None:
        resp = client.get(f"/superadmin/tenants/{seeded['tenant_id']}/logs")
        assert resp.status_code == 401


class TestAuditLogIntegration:
    """Verify that real operations write log entries."""

    def test_tenant_settings_update_writes_log(
        self, client, seeded, auth_header
    ) -> None:
        tenant_id = seeded["tenant_id"]
        # Update settings.
        client.put(
            f"/tenants/{tenant_id}/settings/operations",
            headers=auth_header,
            json={"bot_enabled": False},
        )

        # Check that a log entry was written.
        resp = client.get(
            f"/tenants/{tenant_id}/logs",
            headers=auth_header,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["entries"]) >= 1
        assert body["entries"][0]["event_type"] == "settings_updated"

    def test_superadmin_settings_update_writes_log(
        self, client, seeded, seed_superadmin
    ) -> None:
        tenant_id = seeded["tenant_id"]
        headers = _sa_headers(client, seed_superadmin)
        client.put(
            f"/superadmin/tenants/{tenant_id}/settings/operations",
            headers=headers,
            json={"bot_enabled": False},
        )

        resp = client.get(
            f"/superadmin/tenants/{tenant_id}/logs",
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["entries"]) >= 1
        assert body["entries"][0]["event_type"] == "settings_updated"

    def test_bot_paused_writes_log(
        self, client, seeded, api_session_factory
    ) -> None:
        tenant_id = seeded["tenant_id"]
        from packages.infrastructure.db.models.tenants import TenantSetting

        s = api_session_factory()
        try:
            s.add(TenantSetting(tenant_id=tenant_id, config={
                "bot": {"enabled": False},
                "booking": {"closed_dates": []},
            }))
            s.commit()
        finally:
            s.close()

        # Send a webhook message (bot is disabled).
        client.post(
            f"/webhooks/whatsapp/{tenant_id}",
            json={
                "message": {
                    "id": "wamid.botoff",
                    "from": "+5491100000999",
                    "text": {"body": "hola"},
                    "type": "text",
                },
                "conversation": {},
                "is_new_conversation": False,
                "phone_number_id": "12345",
            },
        )

        # Check logs.
        s2 = api_session_factory()
        try:
            from packages.infrastructure.repositories import TenantAuditLogRepository

            repo = TenantAuditLogRepository(s2, tenant_id)
            entries = repo.list_recent(limit=10)
            assert len(entries) >= 1
            assert entries[0].event_type == "bot_paused"
        finally:
            s2.close()

    def test_closed_date_rejection_writes_log(
        self, client, seeded, auth_header, api_session_factory
    ) -> None:
        tenant_id = seeded["tenant_id"]
        from packages.infrastructure.db.models.tenants import TenantSetting

        s = api_session_factory()
        try:
            s.add(TenantSetting(tenant_id=tenant_id, config={
                "bot": {"enabled": True},
                "booking": {"closed_dates": ["2026-07-15"]},
            }))
            s.commit()
        finally:
            s.close()

        # Try booking on closed date.
        client.post(
            f"/tenants/{tenant_id}/appointments",
            headers=auth_header,
            json={
                "barber_id": str(seeded["barber_id"]),
                "service_id": str(seeded["haircut_service_id"]),
                "start_at": "2026-07-15T10:00:00",
                "customer_name": "Closed Test",
                "customer_phone": "+5491100000111",
            },
        )

        # Check logs.
        s2 = api_session_factory()
        try:
            from packages.infrastructure.repositories import TenantAuditLogRepository

            repo = TenantAuditLogRepository(s2, tenant_id)
            entries = repo.list_recent(limit=10, event_type="booking_closed_date_rejected")
            assert len(entries) >= 1
            assert entries[0].event_type == "booking_closed_date_rejected"
        finally:
            s2.close()

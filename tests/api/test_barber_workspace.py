"""Tests for the barber workspace API endpoint (today's appointments).

Covers:
- Happy path: today's appointments for a barber
- Barber not found
- Auth requirements
- Appointment counts
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from uuid import uuid4

import pytest

from packages.infrastructure.db.models.appointments import Appointment
from packages.infrastructure.db.models.scheduling import Barber


class TestBarberWorkspaceAPI:
    """Test GET /tenants/{id}/workspace/today."""

    def test_happy_path(self, client, auth_header, seeded, api_session_factory):
        """Returns today's appointments for a barber with correct counts."""
        tenant_id = seeded["tenant_id"]
        barber_id = seeded["barber_id"]
        haircut_service_id = seeded["haircut_service_id"]

        # Create some appointments for today
        today = date.today()
        s = api_session_factory()
        try:
            s.add(Appointment(
                id=uuid4(),
                tenant_id=tenant_id,
                barber_id=barber_id,
                service_id=haircut_service_id,
                appointment_date=today,
                start_time=datetime(today.year, today.month, today.day, 10, 0),
                end_time=datetime(today.year, today.month, today.day, 10, 30),
                status="confirmed",
                customer_name="Juan Pérez",
                customer_phone="+5491100000001",
            ))
            s.add(Appointment(
                id=uuid4(),
                tenant_id=tenant_id,
                barber_id=barber_id,
                service_id=haircut_service_id,
                appointment_date=today,
                start_time=datetime(today.year, today.month, today.day, 11, 0),
                end_time=datetime(today.year, today.month, today.day, 11, 30),
                status="pending",
                customer_name="Ana García",
                customer_phone="+5491100000002",
            ))
            s.add(Appointment(
                id=uuid4(),
                tenant_id=tenant_id,
                barber_id=barber_id,
                service_id=haircut_service_id,
                appointment_date=today,
                start_time=datetime(today.year, today.month, today.day, 9, 0),
                end_time=datetime(today.year, today.month, today.day, 9, 30),
                status="completed",
                customer_name="Carlos López",
                customer_phone="+5491100000003",
            ))
            s.add(Appointment(
                id=uuid4(),
                tenant_id=tenant_id,
                barber_id=barber_id,
                service_id=haircut_service_id,
                appointment_date=today,
                start_time=datetime(today.year, today.month, today.day, 14, 0),
                end_time=datetime(today.year, today.month, today.day, 14, 30),
                status="cancelled",
                customer_name="Maria Ruiz",
                customer_phone="+5491100000004",
            ))
            s.commit()
        finally:
            s.close()

        today_str = today.isoformat()
        resp = client.get(
            f"/tenants/{tenant_id}/workspace/today?barber_id={barber_id}&date={today_str}",
            headers=auth_header,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()

        assert body["barber"]["id"] == str(barber_id)
        assert body["barber"]["name"] == "API barber"
        assert body["barber"]["is_active"] is True
        assert body["target_date"] == today_str

        # Counts: 3 active (confirmed + pending + completed)
        # Cancelled appts are excluded by the repo query
        assert body["total"] == 3
        assert body["confirmed"] == 1
        assert body["pending"] == 1
        assert body["completed"] == 1
        assert body["cancelled"] == 0

        # Should have 3 appointments (cancelled excluded from list)
        assert len(body["appointments"]) == 3

        # Ordered by start_time
        assert body["appointments"][0]["customer_name"] == "Carlos López"
        assert body["appointments"][1]["customer_name"] == "Juan Pérez"
        assert body["appointments"][2]["customer_name"] == "Ana García"

    def test_no_appointments(self, client, auth_header, seeded):
        """Returns empty list when no appointments."""
        tenant_id = seeded["tenant_id"]
        barber_id = seeded["barber_id"]
        today_str = date.today().isoformat()

        resp = client.get(
            f"/tenants/{tenant_id}/workspace/today?barber_id={barber_id}&date={today_str}",
            headers=auth_header,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 0
        assert body["appointments"] == []

    def test_barber_not_found(self, client, auth_header, seeded):
        """Non-existent barber -> 404."""
        tenant_id = seeded["tenant_id"]
        fake_id = uuid4()
        today_str = date.today().isoformat()

        resp = client.get(
            f"/tenants/{tenant_id}/workspace/today?barber_id={fake_id}&date={today_str}",
            headers=auth_header,
        )
        assert resp.status_code == 404

    def test_requires_auth(self, client, seeded):
        """No auth -> 401."""
        tenant_id = seeded["tenant_id"]
        barber_id = seeded["barber_id"]
        resp = client.get(
            f"/tenants/{tenant_id}/workspace/today?barber_id={barber_id}",
        )
        assert resp.status_code == 401

    def test_requires_tenant_scope(self, client, seeded, superadmin_header):
        """Superadmin token -> 403."""
        resp = client.get(
            f"/tenants/{seeded['tenant_id']}/workspace/today?barber_id={seeded['barber_id']}",
            headers=superadmin_header,
        )
        assert resp.status_code == 403

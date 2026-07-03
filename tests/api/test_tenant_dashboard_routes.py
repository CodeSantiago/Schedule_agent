"""API tests for Tenant Dashboard Content — Phase 1 backend.

Covers all new PUT/PATCH/DELETE endpoints, auth wiring, and tenant
isolation. Reuses the same `seeded` + `auth_header` fixture pattern
from conftest.
"""

from __future__ import annotations

from datetime import date, time
from uuid import uuid4

import pytest

WEDNESDAY = date(2026, 6, 24)


# --- Helpers --------------------------------------------------------------


def _authz(headers: dict | None) -> dict:
    """Ensure we always pass auth for happy-path tests."""
    return headers or {}


# --- Barber PUT -----------------------------------------------------------


class TestBarberUpdateRoute:
    def test_update_barber_name(self, client, seeded, auth_header) -> None:
        resp = client.put(
            f"/tenants/{seeded['tenant_id']}/barbers/{seeded['barber_id']}",
            json={"name": "Carlos Updated"},
            headers=auth_header,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["name"] == "Carlos Updated"
        assert body["id"] == str(seeded["barber_id"])
        # Unchanged fields preserved.
        assert body["is_active"] is True

    def test_update_barber_soft_delete(self, client, seeded, auth_header) -> None:
        resp = client.put(
            f"/tenants/{seeded['tenant_id']}/barbers/{seeded['barber_id']}",
            json={"is_active": False},
            headers=auth_header,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["is_active"] is False

    def test_update_barber_404(self, client, seeded, auth_header) -> None:
        resp = client.put(
            f"/tenants/{seeded['tenant_id']}/barbers/{uuid4()}",
            json={"name": "Nope"},
            headers=auth_header,
        )
        assert resp.status_code == 404

    def test_update_barber_401(self, client, seeded) -> None:
        resp = client.put(
            f"/tenants/{seeded['tenant_id']}/barbers/{seeded['barber_id']}",
            json={"name": "X"},
        )
        assert resp.status_code == 401

    def test_update_barber_403_wrong_tenant(
        self, client, seeded, auth_header
    ) -> None:
        wrong_tenant = uuid4()
        resp = client.put(
            f"/tenants/{wrong_tenant}/barbers/{seeded['barber_id']}",
            json={"name": "X"},
            headers=auth_header,
        )
        assert resp.status_code == 403


# --- Service PUT ----------------------------------------------------------


class TestServiceUpdateRoute:
    def test_update_service_price_and_duration(
        self, client, seeded, auth_header
    ) -> None:
        resp = client.put(
            f"/tenants/{seeded['tenant_id']}/services/{seeded['haircut_service_id']}",
            json={"price_cents": 1500, "duration_minutes": 45},
            headers=auth_header,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["price_cents"] == 1500
        assert body["duration_minutes"] == 45
        # Unchanged fields preserved.
        assert body["name"] == "Corte"

    def test_update_service_toggle_active(
        self, client, seeded, auth_header
    ) -> None:
        resp = client.put(
            f"/tenants/{seeded['tenant_id']}/services/{seeded['haircut_service_id']}",
            json={"is_active": False},
            headers=auth_header,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["is_active"] is False

    def test_update_service_404(self, client, seeded, auth_header) -> None:
        resp = client.put(
            f"/tenants/{seeded['tenant_id']}/services/{uuid4()}",
            json={"name": "Nope"},
            headers=auth_header,
        )
        assert resp.status_code == 404

    def test_update_service_401(self, client, seeded) -> None:
        resp = client.put(
            f"/tenants/{seeded['tenant_id']}/services/{seeded['haircut_service_id']}",
            json={"name": "X"},
        )
        assert resp.status_code == 401

    def test_update_service_403_wrong_tenant(
        self, client, seeded, auth_header
    ) -> None:
        resp = client.put(
            f"/tenants/{uuid4()}/services/{seeded['haircut_service_id']}",
            json={"name": "X"},
            headers=auth_header,
        )
        assert resp.status_code == 403


# --- Schedule PUT / DELETE -------------------------------------------------


class TestScheduleUpdateRoute:
    @pytest.fixture(autouse=True)
    def _setup(self, seeded):
        self.barber_id = seeded["barber_id"]
        self.tenant_id = seeded["tenant_id"]
        self._schedule_id = None  # set by create_schedule

    def _create_schedule(self, client, auth_header, **overrides) -> dict:
        payload = {"weekday": "mon", "start_time": "09:00", "end_time": "13:00", **overrides}
        resp = client.post(
            f"/tenants/{self.tenant_id}/barbers/{self.barber_id}/schedules",
            json=payload,
            headers=auth_header,
        )
        assert resp.status_code == 201, resp.text
        self._schedule_id = resp.json()["id"]
        return resp.json()

    def test_update_schedule_hours(self, client, seeded, auth_header) -> None:
        sched = self._create_schedule(client, auth_header)
        resp = client.put(
            f"/tenants/{self.tenant_id}/barbers/{self.barber_id}/schedules/{sched['id']}",
            json={"start_time": "09:00", "end_time": "14:00"},
            headers=auth_header,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["end_time"] == "14:00:00"

    def test_update_schedule_404(self, client, seeded, auth_header) -> None:
        resp = client.put(
            f"/tenants/{self.tenant_id}/barbers/{self.barber_id}/schedules/{uuid4()}",
            json={"start_time": "09:00"},
            headers=auth_header,
        )
        assert resp.status_code == 404

    def test_delete_schedule_happy(self, client, seeded, auth_header) -> None:
        sched = self._create_schedule(client, auth_header)
        resp = client.delete(
            f"/tenants/{self.tenant_id}/barbers/{self.barber_id}/schedules/{sched['id']}",
            headers=auth_header,
        )
        assert resp.status_code == 204, resp.text

    def test_delete_schedule_404(self, client, seeded, auth_header) -> None:
        resp = client.delete(
            f"/tenants/{self.tenant_id}/barbers/{self.barber_id}/schedules/{uuid4()}",
            headers=auth_header,
        )
        assert resp.status_code == 404

    def test_schedule_401(self, client, seeded) -> None:
        resp = client.put(
            f"/tenants/{self.tenant_id}/barbers/{self.barber_id}/schedules/{uuid4()}",
            json={"start_time": "09:00"},
        )
        assert resp.status_code == 401

    def test_delete_schedule_401(self, client, seeded) -> None:
        resp = client.delete(
            f"/tenants/{self.tenant_id}/barbers/{self.barber_id}/schedules/{uuid4()}",
        )
        assert resp.status_code == 401

    def test_schedule_403_wrong_tenant(
        self, client, seeded, auth_header
    ) -> None:
        sched = self._create_schedule(client, auth_header)
        wrong_tenant = uuid4()
        resp = client.put(
            f"/tenants/{wrong_tenant}/barbers/{self.barber_id}/schedules/{sched['id']}",
            json={"start_time": "09:00"},
            headers=auth_header,
        )
        assert resp.status_code == 403


# --- Absence PUT ----------------------------------------------------------


class TestAbsenceUpdateRoute:
    @pytest.fixture(autouse=True)
    def _setup(self, seeded):
        self.barber_id = seeded["barber_id"]
        self.tenant_id = seeded["tenant_id"]

    def _create_absence(self, client, auth_header, **overrides) -> dict:
        payload = {"absence_date": "2026-07-15", "reason": "Vacation", **overrides}
        resp = client.post(
            f"/tenants/{self.tenant_id}/barbers/{self.barber_id}/absences",
            json=payload,
            headers=auth_header,
        )
        assert resp.status_code == 201, resp.text
        return resp.json()

    def test_update_absence_reason(self, client, seeded, auth_header) -> None:
        absence = self._create_absence(client, auth_header)
        resp = client.put(
            f"/tenants/{self.tenant_id}/barbers/{self.barber_id}/absences/{absence['id']}",
            json={"reason": "Doctor visit"},
            headers=auth_header,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["reason"] == "Doctor visit"

    def test_update_absence_partial_day(
        self, client, seeded, auth_header
    ) -> None:
        absence = self._create_absence(client, auth_header)
        resp = client.put(
            f"/tenants/{self.tenant_id}/barbers/{self.barber_id}/absences/{absence['id']}",
            json={"start_time": "14:00", "end_time": "17:00"},
            headers=auth_header,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["start_time"] == "14:00:00"
        assert body["end_time"] == "17:00:00"

    def test_update_absence_404(self, client, seeded, auth_header) -> None:
        resp = client.put(
            f"/tenants/{self.tenant_id}/barbers/{self.barber_id}/absences/{uuid4()}",
            json={"reason": "Nope"},
            headers=auth_header,
        )
        assert resp.status_code == 404

    def test_update_absence_401(self, client, seeded) -> None:
        resp = client.put(
            f"/tenants/{self.tenant_id}/barbers/{self.barber_id}/absences/{uuid4()}",
            json={"reason": "X"},
        )
        assert resp.status_code == 401


# --- Extra-hour PUT -------------------------------------------------------


class TestExtraHourUpdateRoute:
    @pytest.fixture(autouse=True)
    def _setup(self, seeded):
        self.barber_id = seeded["barber_id"]
        self.tenant_id = seeded["tenant_id"]

    def _create_extra_hour(self, client, auth_header, **overrides) -> dict:
        payload = {
            "extra_date": "2026-07-18",
            "start_time": "10:00",
            "end_time": "14:00",
            "reason": "Saturday coverage",
            **overrides,
        }
        resp = client.post(
            f"/tenants/{self.tenant_id}/barbers/{self.barber_id}/extra-hours",
            json=payload,
            headers=auth_header,
        )
        assert resp.status_code == 201, resp.text
        return resp.json()

    def test_update_extra_hour_hours(
        self, client, seeded, auth_header
    ) -> None:
        eh = self._create_extra_hour(client, auth_header)
        resp = client.put(
            f"/tenants/{self.tenant_id}/barbers/{self.barber_id}/extra-hours/{eh['id']}",
            json={"start_time": "09:00", "end_time": "15:00"},
            headers=auth_header,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["start_time"] == "09:00:00"
        assert body["end_time"] == "15:00:00"

    def test_update_extra_hour_404(self, client, seeded, auth_header) -> None:
        resp = client.put(
            f"/tenants/{self.tenant_id}/barbers/{self.barber_id}/extra-hours/{uuid4()}",
            json={"start_time": "09:00"},
            headers=auth_header,
        )
        assert resp.status_code == 404

    def test_update_extra_hour_401(self, client, seeded) -> None:
        resp = client.put(
            f"/tenants/{self.tenant_id}/barbers/{self.barber_id}/extra-hours/{uuid4()}",
            json={"start_time": "09:00"},
        )
        assert resp.status_code == 401


# --- Appointment status PATCH ---------------------------------------------


class TestAppointmentStatusRoute:
    @pytest.fixture(autouse=True)
    def _auth(self, auth_header):
        self.headers = auth_header

    def _book_haircut(self, client, seeded, hh: int = 11, name: str = "Ada") -> dict:
        resp = client.post(
            f"/tenants/{seeded['tenant_id']}/appointments",
            json={
                "barber_id": str(seeded["barber_id"]),
                "service_id": str(seeded["haircut_service_id"]),
                "start_at": f"{WEDNESDAY.isoformat()}T{hh:02d}:00:00",
                "customer_name": name,
                "customer_phone": "+5491100000001",
            },
            headers=self.headers,
        )
        assert resp.status_code == 201, resp.text
        return resp.json()

    def test_confirm_appointment(self, client, seeded) -> None:
        booked = self._book_haircut(client, seeded)
        resp = client.patch(
            f"/tenants/{seeded['tenant_id']}/appointments/{booked['appointment']['id']}/status",
            json={"status": "confirmed"},
            headers=self.headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "confirmed"

    def test_mark_appointment_completed(self, client, seeded) -> None:
        booked = self._book_haircut(client, seeded)
        # First confirm.
        client.patch(
            f"/tenants/{seeded['tenant_id']}/appointments/{booked['appointment']['id']}/status",
            json={"status": "confirmed"},
            headers=self.headers,
        )
        resp = client.patch(
            f"/tenants/{seeded['tenant_id']}/appointments/{booked['appointment']['id']}/status",
            json={"status": "completed"},
            headers=self.headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "completed"

    def test_mark_appointment_no_show(self, client, seeded) -> None:
        booked = self._book_haircut(client, seeded)
        client.patch(
            f"/tenants/{seeded['tenant_id']}/appointments/{booked['appointment']['id']}/status",
            json={"status": "confirmed"},
            headers=self.headers,
        )
        resp = client.patch(
            f"/tenants/{seeded['tenant_id']}/appointments/{booked['appointment']['id']}/status",
            json={"status": "no_show"},
            headers=self.headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "no_show"

    def test_patch_status_404(self, client, seeded, auth_header) -> None:
        resp = client.patch(
            f"/tenants/{seeded['tenant_id']}/appointments/{uuid4()}/status",
            json={"status": "confirmed"},
            headers=auth_header,
        )
        assert resp.status_code == 404

    def test_patch_status_invalid_status(
        self, client, seeded, auth_header
    ) -> None:
        resp = client.patch(
            f"/tenants/{seeded['tenant_id']}/appointments/{uuid4()}/status",
            json={"status": "invalid"},
            headers=auth_header,
        )
        assert resp.status_code == 422

    def test_patch_status_401(self, client, seeded) -> None:
        resp = client.patch(
            f"/tenants/{seeded['tenant_id']}/appointments/{uuid4()}/status",
            json={"status": "confirmed"},
        )
        assert resp.status_code == 401


# --- Auth on existing routes (regression) ---------------------------------


class TestExistingRoutesAuth:
    """Existing GET/POST routes must also 401 without valid auth."""

    def test_list_barbers_401(self, client, seeded) -> None:
        resp = client.get(f"/tenants/{seeded['tenant_id']}/barbers")
        assert resp.status_code == 401

    def test_create_barber_401(self, client, seeded) -> None:
        resp = client.post(
            f"/tenants/{seeded['tenant_id']}/barbers",
            json={"name": "X"},
        )
        assert resp.status_code == 401

    def test_get_barber_401(self, client, seeded) -> None:
        resp = client.get(
            f"/tenants/{seeded['tenant_id']}/barbers/{seeded['barber_id']}"
        )
        assert resp.status_code == 401

    def test_list_services_401(self, client, seeded) -> None:
        resp = client.get(f"/tenants/{seeded['tenant_id']}/services")
        assert resp.status_code == 401

    def test_create_service_401(self, client, seeded) -> None:
        resp = client.post(
            f"/tenants/{seeded['tenant_id']}/services",
            json={"name": "X", "duration_minutes": 30},
        )
        assert resp.status_code == 401

    def test_get_service_401(self, client, seeded) -> None:
        resp = client.get(
            f"/tenants/{seeded['tenant_id']}/services/{seeded['haircut_service_id']}"
        )
        assert resp.status_code == 401

    def test_list_schedules_401(self, client, seeded) -> None:
        resp = client.get(
            f"/tenants/{seeded['tenant_id']}/barbers/{seeded['barber_id']}/schedules"
        )
        assert resp.status_code == 401

    def test_create_schedule_401(self, client, seeded) -> None:
        resp = client.post(
            f"/tenants/{seeded['tenant_id']}/barbers/{seeded['barber_id']}/schedules",
            json={"weekday": "mon", "start_time": "09:00", "end_time": "17:00"},
        )
        assert resp.status_code == 401

    def test_list_absences_401(self, client, seeded) -> None:
        resp = client.get(
            f"/tenants/{seeded['tenant_id']}/barbers/{seeded['barber_id']}/absences"
        )
        assert resp.status_code == 401

    def test_create_absence_401(self, client, seeded) -> None:
        resp = client.post(
            f"/tenants/{seeded['tenant_id']}/barbers/{seeded['barber_id']}/absences",
            json={"absence_date": "2026-07-15"},
        )
        assert resp.status_code == 401

    def test_delete_absence_401(self, client, seeded) -> None:
        resp = client.delete(
            f"/tenants/{seeded['tenant_id']}/barbers/{seeded['barber_id']}/absences/{uuid4()}"
        )
        assert resp.status_code == 401

    def test_list_extra_hours_401(self, client, seeded) -> None:
        resp = client.get(
            f"/tenants/{seeded['tenant_id']}/barbers/{seeded['barber_id']}/extra-hours"
        )
        assert resp.status_code == 401

    def test_create_extra_hour_401(self, client, seeded) -> None:
        resp = client.post(
            f"/tenants/{seeded['tenant_id']}/barbers/{seeded['barber_id']}/extra-hours",
            json={"extra_date": "2026-07-18", "start_time": "10:00", "end_time": "14:00"},
        )
        assert resp.status_code == 401

    def test_delete_extra_hour_401(self, client, seeded) -> None:
        resp = client.delete(
            f"/tenants/{seeded['tenant_id']}/barbers/{seeded['barber_id']}/extra-hours/{uuid4()}"
        )
        assert resp.status_code == 401

    def test_list_appointments_401(self, client, seeded) -> None:
        resp = client.get(
            f"/tenants/{seeded['tenant_id']}/appointments",
            params={
                "barber_id": str(seeded["barber_id"]),
                "date_from": "2026-07-15",
                "date_to": "2026-07-15",
            },
        )
        assert resp.status_code == 401

    def test_create_appointment_401(self, client, seeded) -> None:
        resp = client.post(
            f"/tenants/{seeded['tenant_id']}/appointments",
            json={
                "barber_id": str(seeded["barber_id"]),
                "service_id": str(seeded["haircut_service_id"]),
                "start_at": f"{WEDNESDAY.isoformat()}T11:00:00",
                "customer_name": "X",
                "customer_phone": "+5491100000001",
            },
        )
        assert resp.status_code == 401

    def test_delete_appointment_401(self, client, seeded) -> None:
        resp = client.delete(
            f"/tenants/{seeded['tenant_id']}/appointments/{uuid4()}"
        )
        assert resp.status_code == 401

    def test_get_overview_401(self, client, seeded) -> None:
        resp = client.get(
            f"/tenants/{seeded['tenant_id']}/overview?date=2026-07-15"
        )
        assert resp.status_code == 401

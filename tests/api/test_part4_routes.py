"""API tests for the Part 4 endpoints: cancel, reschedule, overview.

The conftest already wires `client` and `api_engine`; we reuse the
`seeded` fixture (tenant + barber + Haircut + CB services + a wed
schedule) to focus on the new routes.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest


WEDNESDAY = date(2026, 6, 24)


# --- Cancel ---------------------------------------------------------------


class TestCancelRoute:
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

    def test_cancel_returns_cancelled_row(self, client, seeded) -> None:
        booked = self._book_haircut(client, seeded)
        resp = client.delete(
            f"/tenants/{seeded['tenant_id']}/appointments/{booked['appointment']['id']}",
            headers=self.headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["appointment"]["status"] == "cancelled"
        assert body["continuation"] is None

    def test_cancel_404_when_id_unknown(self, client, seeded) -> None:
        from uuid import uuid4

        resp = client.delete(
            f"/tenants/{seeded['tenant_id']}/appointments/{uuid4()}",
            headers=self.headers,
        )
        assert resp.status_code == 404

    def test_cancel_cb_primary_cancels_partner(self, client, seeded) -> None:
        booked = client.post(
            f"/tenants/{seeded['tenant_id']}/appointments",
            json={
                "barber_id": str(seeded["barber_id"]),
                "service_id": str(seeded["cb_id"]),
                "start_at": f"{WEDNESDAY.isoformat()}T11:00:00",
                "customer_name": "Bob",
                "customer_phone": "+5491100000099",
            },
            headers=self.headers,
        )
        assert booked.status_code == 201, booked.text
        body = booked.json()
        primary_id = body["appointment"]["id"]
        cont_id = body["continuation"]["id"]

        resp = client.delete(
            f"/tenants/{seeded['tenant_id']}/appointments/{primary_id}",
            headers=self.headers,
        )
        assert resp.status_code == 200, resp.text
        out = resp.json()
        assert out["continuation"] is not None
        assert out["continuation"]["id"] == cont_id
        assert out["continuation"]["status"] == "cancelled"

    def test_double_cancel_returns_409(self, client, seeded) -> None:
        booked = self._book_haircut(client, seeded)
        first = client.delete(
            f"/tenants/{seeded['tenant_id']}/appointments/{booked['appointment']['id']}",
            headers=self.headers,
        )
        assert first.status_code == 200
        second = client.delete(
            f"/tenants/{seeded['tenant_id']}/appointments/{booked['appointment']['id']}",
            headers=self.headers,
        )
        assert second.status_code == 409


# --- Reschedule ----------------------------------------------------------


class TestRescheduleRoute:
    @pytest.fixture(autouse=True)
    def _auth(self, auth_header):
        self.headers = auth_header

    def test_reschedule_to_a_free_slot_succeeds(self, client, seeded) -> None:
        booked = client.post(
            f"/tenants/{seeded['tenant_id']}/appointments",
            json={
                "barber_id": str(seeded["barber_id"]),
                "service_id": str(seeded["haircut_service_id"]),
                "start_at": f"{WEDNESDAY.isoformat()}T11:00:00",
                "customer_name": "Ada",
                "customer_phone": "+5491100000001",
            },
            headers=self.headers,
        )
        assert booked.status_code == 201
        appt_id = booked.json()["appointment"]["id"]

        resp = client.post(
            f"/tenants/{seeded['tenant_id']}/appointments/{appt_id}/reschedule",
            json={"new_start_at": f"{WEDNESDAY.isoformat()}T13:30:00"},
            headers=self.headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["appointment"]["id"] == appt_id
        assert body["appointment"]["start_time"].endswith("13:30:00")
        assert body["continuation"] is None

    def test_reschedule_to_taken_slot_returns_409(self, client, seeded) -> None:
        # Book 11:00 and 13:00, then move 11:00 to 13:00.
        for hh, name in [(11, "A"), (13, "B")]:
            r = client.post(
                f"/tenants/{seeded['tenant_id']}/appointments",
                json={
                    "barber_id": str(seeded["barber_id"]),
                    "service_id": str(seeded["haircut_service_id"]),
                    "start_at": f"{WEDNESDAY.isoformat()}T{hh:02d}:00:00",
                    "customer_name": name,
                    "customer_phone": f"+5491100000{hh:03d}",
                },
                headers=self.headers,
            )
            assert r.status_code == 201, r.text
        appt_id = client.get(
            f"/tenants/{seeded['tenant_id']}/daily?date={WEDNESDAY.isoformat()}"
        ).json()[0]["id"]

        resp = client.post(
            f"/tenants/{seeded['tenant_id']}/appointments/{appt_id}/reschedule",
            json={"new_start_at": f"{WEDNESDAY.isoformat()}T13:00:00"},
            headers=self.headers,
        )
        assert resp.status_code in (400, 409), resp.text

    def test_reschedule_cb_moves_both_rows(self, client, seeded) -> None:
        booked = client.post(
            f"/tenants/{seeded['tenant_id']}/appointments",
            json={
                "barber_id": str(seeded["barber_id"]),
                "service_id": str(seeded["cb_id"]),
                "start_at": f"{WEDNESDAY.isoformat()}T11:00:00",
                "customer_name": "Bob",
                "customer_phone": "+5491100000099",
            },
            headers=self.headers,
        )
        assert booked.status_code == 201
        primary_id = booked.json()["appointment"]["id"]
        cont_id = booked.json()["continuation"]["id"]

        resp = client.post(
            f"/tenants/{seeded['tenant_id']}/appointments/{primary_id}/reschedule",
            json={"new_start_at": f"{WEDNESDAY.isoformat()}T15:00:00"},
            headers=self.headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["appointment"]["start_time"].endswith("15:00:00")
        assert body["continuation"] is not None
        assert body["continuation"]["id"] == cont_id
        assert body["continuation"]["start_time"].endswith("15:30:00")

    def test_reschedule_unknown_id_returns_404(self, client, seeded) -> None:
        from uuid import uuid4

        resp = client.post(
            f"/tenants/{seeded['tenant_id']}/appointments/{uuid4()}/reschedule",
            json={"new_start_at": f"{WEDNESDAY.isoformat()}T13:30:00"},
            headers=self.headers,
        )
        assert resp.status_code == 404


# --- Overview ------------------------------------------------------------


class TestOverviewRoute:
    @pytest.fixture(autouse=True)
    def _auth(self, auth_header):
        self.headers = auth_header

    def test_overview_for_a_day_with_no_bookings(self, client, seeded) -> None:
        resp = client.get(
            f"/tenants/{seeded['tenant_id']}/overview?date=2026-12-31",
            headers=self.headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["date"] == "2026-12-31"
        assert body["counts"]["booked_today"] == 0
        assert body["appointments"] == []
        # 7 upcoming buckets
        assert len(body["upcoming"]) == 7

    def test_overview_after_a_booking(self, client, seeded) -> None:
        booked = client.post(
            f"/tenants/{seeded['tenant_id']}/appointments",
            json={
                "barber_id": str(seeded["barber_id"]),
                "service_id": str(seeded["haircut_service_id"]),
                "start_at": f"{WEDNESDAY.isoformat()}T11:00:00",
                "customer_name": "Overview",
                "customer_phone": "+5491100000099",
            },
            headers=self.headers,
        )
        assert booked.status_code == 201
        resp = client.get(
            f"/tenants/{seeded['tenant_id']}/overview?date={WEDNESDAY.isoformat()}",
            headers=self.headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["counts"]["booked_today"] == 1
        assert body["counts"]["pending_today"] == 1
        assert len(body["appointments"]) == 1
        a = body["appointments"][0]
        assert a["customer_name"] == "Overview"
        assert a["barber_name"] == "API barber"
        assert a["service_name"] == "Corte"

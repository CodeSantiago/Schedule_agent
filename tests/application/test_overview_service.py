"""Tests for the operational overview service.

The overview service is a thin read layer; the goal of these tests
is to pin the shape of the response (so the dashboard template can
bind against it without surprise) and to cover the small but easy
edges: empty day, day with all status kinds, CB continuation flag.
"""

from __future__ import annotations

from datetime import date, datetime, time
from uuid import uuid4

import pytest

from packages.application.scheduling.booking_service import (
    BookSlotCommand,
    BookingService,
)
from packages.application.scheduling.overview_service import OverviewService
from packages.infrastructure.db.models.scheduling import (
    Barber,
    BarberSchedule,
    Service,
)
from packages.infrastructure.db.models.tenants import Tenant


WEDNESDAY = date(2026, 6, 24)
NOW = datetime(2026, 6, 24, 9, 0, 0)


def _seed(session):
    tenant = Tenant(
        id=uuid4(),
        name="OV",
        slug=f"ov-{uuid4().hex[:6]}",
        status="trial",
        timezone="UTC",
    )
    session.add(tenant)
    session.flush()
    barber = Barber(
        id=uuid4(), tenant_id=tenant.id, name="O", is_active=True
    )
    session.add(barber)
    session.flush()
    haircut_service = Service(
        id=uuid4(),
        tenant_id=tenant.id,
        name="Corte",
        code="C",
        duration_minutes=30,
        is_active=True,
    )
    cb_service = Service(
        id=uuid4(),
        tenant_id=tenant.id,
        name="CB",
        code="CB",
        duration_minutes=60,
        is_active=True,
    )
    sch = BarberSchedule(
        id=uuid4(),
        barber_id=barber.id,
        weekday="wed",
        start_time=time(10, 0),
        end_time=time(20, 0),
    )
    session.add_all([haircut_service, cb_service, sch])
    session.commit()
    return tenant, barber, haircut_service, cb_service


class TestOverview:
    def test_empty_day(self, session) -> None:
        tenant, _, _, _ = _seed(session)
        svc = OverviewService(session, tenant.id)
        result = svc.build(WEDNESDAY)
        assert result.tenant_id == str(tenant.id)
        assert result.target_date == WEDNESDAY.isoformat()
        assert result.counts.booked_today == 0
        assert result.counts.cancelled_today == 0
        assert result.appointments == []
        assert len(result.upcoming) == 7

    def test_day_with_a_haircut_booking(self, session) -> None:
        tenant, barber, haircut_service, _ = _seed(session)
        bs = BookingService(session, tenant.id)
        bs.book_slot(
            BookSlotCommand(
                tenant_id=tenant.id,
                barber_id=barber.id,
                service_id=haircut_service.id,
                start_at=datetime.combine(WEDNESDAY, time(11, 0)),
                customer_name="Ada",
                customer_phone="+5491100000001",
            )
        )
        session.commit()

        svc = OverviewService(session, tenant.id)
        result = svc.build(WEDNESDAY)
        assert result.counts.booked_today == 1
        assert result.counts.pending_today == 1
        assert len(result.appointments) == 1
        first = result.appointments[0]
        assert first.barber_name == "O"
        assert first.service_name == "Corte"
        assert first.customer_name == "Ada"
        assert first.is_cb_continuation is False

    def test_day_with_cb_marks_continuation(self, session) -> None:
        tenant, barber, _, cb_service = _seed(session)
        bs = BookingService(session, tenant.id)
        bs.book_slot(
            BookSlotCommand(
                tenant_id=tenant.id,
                barber_id=barber.id,
                service_id=cb_service.id,
                start_at=datetime.combine(WEDNESDAY, time(11, 0)),
                customer_name="Bob",
                customer_phone="+5491100000002",
            )
        )
        session.commit()

        svc = OverviewService(session, tenant.id)
        result = svc.build(WEDNESDAY)
        # Two rows for the CB (primary + continuation).
        assert len(result.appointments) == 2
        flags = [a.is_cb_continuation for a in result.appointments]
        assert flags.count(True) == 1
        assert flags.count(False) == 1

    def test_cancelled_appointment_counted_but_not_listed(
        self, session
    ) -> None:
        tenant, barber, haircut_service, _ = _seed(session)
        bs = BookingService(session, tenant.id)
        res = bs.book_slot(
            BookSlotCommand(
                tenant_id=tenant.id,
                barber_id=barber.id,
                service_id=haircut_service.id,
                start_at=datetime.combine(WEDNESDAY, time(11, 0)),
                customer_name="To Cancel",
                customer_phone="+5491100000003",
            )
        )
        session.commit()
        # Mark cancelled.
        res.appointment.status = "cancelled"
        session.commit()

        svc = OverviewService(session, tenant.id)
        result = svc.build(WEDNESDAY)
        assert result.counts.cancelled_today == 1
        assert result.counts.booked_today == 0
        # The cancelled row is NOT in the list (we only render active).
        assert result.appointments == []

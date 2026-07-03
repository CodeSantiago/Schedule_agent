"""Tests for AppointmentManageService (cancel + reschedule).

These run against the in-memory sqlite engine (via the conftest's
`engine` + `session` fixtures) and exercise the full
load → plan → persist path of the management service. The CB pair
behaviour is the critical regression: cancelling or rescheduling a CB
primary must also touch the continuation row, and vice versa.
"""

from __future__ import annotations

from datetime import date, datetime, time
from uuid import uuid4

import pytest

from packages.application.scheduling.booking_service import (
    BookSlotCommand,
    BookingService,
)
from packages.application.scheduling.manage_service import (
    AppointmentManageService,
)
from packages.domain.scheduling.errors import (
    AppointmentAlreadyCancelledError,
    AppointmentInPastError,
    AppointmentNotFoundError,
    AppointmentNotReschedulableError,
)
from packages.infrastructure.db.models.appointments import Appointment
from packages.infrastructure.db.models.scheduling import (
    Barber,
    BarberSchedule,
    Service,
)
from packages.infrastructure.db.models.tenants import Tenant
from packages.infrastructure.repositories import (
    AppointmentRepository,
    BarberRepository,
    ServiceRepository,
)


WEDNESDAY = date(2026, 6, 24)
THURSDAY = date(2026, 6, 25)
FUTURE_NOW = datetime(2026, 6, 24, 9, 0, 0)


def _seed_tenant(session):
    tenant = Tenant(
        id=uuid4(),
        name="Manage",
        slug=f"manage-{uuid4().hex[:6]}",
        status="trial",
        timezone="UTC",
    )
    session.add(tenant)
    session.flush()
    return tenant


def _seed_barber(session, tenant_id):
    b = Barber(
        id=uuid4(),
        tenant_id=tenant_id,
        name="Bob",
        is_active=True,
    )
    session.add(b)
    session.flush()
    return b


def _seed_service(session, tenant_id, *, code="C", name="Corte", duration=30):
    s = Service(
        id=uuid4(),
        tenant_id=tenant_id,
        name=name,
        code=code,
        duration_minutes=duration,
        price_cents=0,
        is_active=True,
    )
    session.add(s)
    session.flush()
    return s


def _seed_schedule(session, barber_id):
    sch = BarberSchedule(
        id=uuid4(),
        barber_id=barber_id,
        weekday="wed",
        start_time=time(10, 0),
        end_time=time(20, 0),
    )
    session.add(sch)
    session.flush()
    return sch


def _book(session, tenant_id, barber_id, service, start_at, *, name="Ada"):
    svc = BookingService(session, tenant_id)
    res = svc.book_slot(
        BookSlotCommand(
            tenant_id=tenant_id,
            barber_id=barber_id,
            service_id=service.id,
            start_at=start_at,
            customer_name=name,
            customer_phone="+5491100000000",
        )
    )
    session.commit()
    return res


# --- Cancel ---------------------------------------------------------------


class TestCancelSingle:
    def test_cancels_a_single_slot_appointment(self, session) -> None:
        tenant = _seed_tenant(session)
        barber = _seed_barber(session, tenant.id)
        service = _seed_service(session, tenant.id)
        _seed_schedule(session, barber.id)
        res = _book(session, tenant.id, barber.id, service,
                    datetime.combine(WEDNESDAY, time(11, 0)))

        mgr = AppointmentManageService(session, tenant.id)
        outcome = mgr.cancel(res.appointment.id, now=FUTURE_NOW)
        session.commit()

        assert outcome.cancelled.status == "cancelled"
        assert outcome.continuation_cancelled is None

        # The DB row is updated.
        repo = AppointmentRepository(session, tenant.id)
        row = repo.get_by_id(res.appointment.id)
        assert row is not None
        assert row.status == "cancelled"

    def test_cancel_missing_raises_not_found(self, session) -> None:
        tenant = _seed_tenant(session)
        mgr = AppointmentManageService(session, tenant.id)
        with pytest.raises(AppointmentNotFoundError):
            mgr.cancel(uuid4(), now=FUTURE_NOW)

    def test_cancel_twice_raises(self, session) -> None:
        tenant = _seed_tenant(session)
        barber = _seed_barber(session, tenant.id)
        service = _seed_service(session, tenant.id)
        _seed_schedule(session, barber.id)
        res = _book(session, tenant.id, barber.id, service,
                    datetime.combine(WEDNESDAY, time(11, 0)))
        mgr = AppointmentManageService(session, tenant.id)
        mgr.cancel(res.appointment.id, now=FUTURE_NOW)
        session.commit()
        with pytest.raises(AppointmentAlreadyCancelledError):
            mgr.cancel(res.appointment.id, now=FUTURE_NOW)

    def test_cancel_past_raises(self, session) -> None:
        tenant = _seed_tenant(session)
        barber = _seed_barber(session, tenant.id)
        service = _seed_service(session, tenant.id)
        _seed_schedule(session, barber.id)
        res = _book(session, tenant.id, barber.id, service,
                    datetime.combine(WEDNESDAY, time(11, 0)))
        mgr = AppointmentManageService(session, tenant.id)
        # Pick a `now` strictly after the slot start on the same day.
        past_now = datetime.combine(WEDNESDAY, time(11, 30))
        with pytest.raises(AppointmentInPastError):
            mgr.cancel(res.appointment.id, now=past_now)


class TestCancelCb:
    def test_cancel_cb_primary_also_cancels_continuation(
        self, session
    ) -> None:
        tenant = _seed_tenant(session)
        barber = _seed_barber(session, tenant.id)
        cb = _seed_service(session, tenant.id, code="CB", name="CB", duration=60)
        _seed_schedule(session, barber.id)
        res = _book(session, tenant.id, barber.id, cb,
                    datetime.combine(WEDNESDAY, time(11, 0)), name="Bob")
        assert res.continuation is not None
        cont_id = res.continuation.id

        mgr = AppointmentManageService(session, tenant.id)
        outcome = mgr.cancel(res.appointment.id, now=FUTURE_NOW)
        session.commit()

        assert outcome.continuation_cancelled is not None
        assert outcome.continuation_cancelled.id == cont_id

        # Both rows are cancelled.
        repo = AppointmentRepository(session, tenant.id)
        primary = repo.get_by_id(res.appointment.id)
        partner = repo.get_by_id(cont_id)
        assert primary is not None and primary.status == "cancelled"
        assert partner is not None and partner.status == "cancelled"

    def test_cancel_cb_continuation_also_cancels_primary(
        self, session
    ) -> None:
        tenant = _seed_tenant(session)
        barber = _seed_barber(session, tenant.id)
        cb = _seed_service(session, tenant.id, code="CB", name="CB", duration=60)
        _seed_schedule(session, barber.id)
        res = _book(session, tenant.id, barber.id, cb,
                    datetime.combine(WEDNESDAY, time(11, 0)))
        assert res.continuation is not None
        primary_id = res.appointment.id

        mgr = AppointmentManageService(session, tenant.id)
        # Cancel the continuation row directly.
        mgr.cancel(res.continuation.id, now=FUTURE_NOW)
        session.commit()

        repo = AppointmentRepository(session, tenant.id)
        primary = repo.get_by_id(primary_id)
        cont = repo.get_by_id(res.continuation.id)
        assert primary is not None and primary.status == "cancelled"
        assert cont is not None and cont.status == "cancelled"


# --- Reschedule ----------------------------------------------------------


class TestReschedule:
    def test_reschedule_moves_to_a_free_slot(self, session) -> None:
        tenant = _seed_tenant(session)
        barber = _seed_barber(session, tenant.id)
        service = _seed_service(session, tenant.id)
        _seed_schedule(session, barber.id)
        res = _book(session, tenant.id, barber.id, service,
                    datetime.combine(WEDNESDAY, time(11, 0)))
        original_id = res.appointment.id
        original_start = res.appointment.start_time

        mgr = AppointmentManageService(session, tenant.id)
        new_start = datetime.combine(WEDNESDAY, time(13, 30))
        outcome = mgr.reschedule(original_id, new_start, now=FUTURE_NOW)
        session.commit()

        assert outcome.appointment.id == original_id
        assert outcome.appointment.start_time == new_start
        assert outcome.appointment.start_time != original_start

    def test_reschedule_to_taken_slot_raises(self, session) -> None:
        tenant = _seed_tenant(session)
        barber = _seed_barber(session, tenant.id)
        service = _seed_service(session, tenant.id)
        _seed_schedule(session, barber.id)
        # Book two slots: 11:00 and 13:00.
        first = _book(session, tenant.id, barber.id, service,
                      datetime.combine(WEDNESDAY, time(11, 0)), name="A")
        _book(session, tenant.id, barber.id, service,
              datetime.combine(WEDNESDAY, time(13, 0)), name="B")

        mgr = AppointmentManageService(session, tenant.id)
        # Move `first` onto 13:00 — already taken.
        with pytest.raises(Exception):
            mgr.reschedule(first.appointment.id, datetime.combine(WEDNESDAY, time(13, 0)),
                           now=FUTURE_NOW)
        session.rollback()

    def test_reschedule_completed_raises(self, session) -> None:
        tenant = _seed_tenant(session)
        barber = _seed_barber(session, tenant.id)
        service = _seed_service(session, tenant.id)
        _seed_schedule(session, barber.id)
        res = _book(session, tenant.id, barber.id, service,
                    datetime.combine(WEDNESDAY, time(11, 0)))
        # Force status=completed.
        res.appointment.status = "completed"
        session.commit()

        mgr = AppointmentManageService(session, tenant.id)
        with pytest.raises(AppointmentNotReschedulableError):
            mgr.reschedule(res.appointment.id,
                           datetime.combine(WEDNESDAY, time(13, 0)),
                           now=FUTURE_NOW)

    def test_reschedule_cb_moves_both_rows(self, session) -> None:
        tenant = _seed_tenant(session)
        barber = _seed_barber(session, tenant.id)
        cb = _seed_service(session, tenant.id, code="CB", name="CB", duration=60)
        _seed_schedule(session, barber.id)
        res = _book(session, tenant.id, barber.id, cb,
                    datetime.combine(WEDNESDAY, time(11, 0)))
        primary_id = res.appointment.id
        cont_id = res.continuation.id

        mgr = AppointmentManageService(session, tenant.id)
        # Move to 15:00 — both halves should land at 15:00 + 15:30.
        new_start = datetime.combine(WEDNESDAY, time(15, 0))
        outcome = mgr.reschedule(primary_id, new_start, now=FUTURE_NOW)
        session.commit()

        assert outcome.appointment.start_time == new_start
        assert outcome.continuation is not None
        assert outcome.continuation.start_time == datetime.combine(WEDNESDAY, time(15, 30))
        assert outcome.continuation.id == cont_id

    def test_reschedule_cb_across_day_boundary_moves_both_rows(
        self, session
    ) -> None:
        # Regression: cross-day CB reschedule used to drop the
        # continuation row. The primary's `appointment_date` was mutated
        # in place BEFORE the partner lookup, so the partner query
        # ran against the new date and missed the (still-old-date)
        # continuation row. The fix saves the old date and uses it
        # for the partner lookup.
        tenant = _seed_tenant(session)
        barber = _seed_barber(session, tenant.id)
        cb = _seed_service(session, tenant.id, code="CB", name="CB", duration=60)
        # The barber works both Wednesday and Thursday 10-20.
        for wd in ("wed", "thu"):
            sch = BarberSchedule(
                id=uuid4(),
                barber_id=barber.id,
                weekday=wd,
                start_time=time(10, 0),
                end_time=time(20, 0),
            )
            session.add(sch)
        session.flush()
        res = _book(session, tenant.id, barber.id, cb,
                    datetime.combine(WEDNESDAY, time(11, 0)))
        primary_id = res.appointment.id
        cont_id = res.continuation.id

        mgr = AppointmentManageService(session, tenant.id)
        # Move the whole pair to Thursday at 15:00.
        new_start = datetime.combine(THURSDAY, time(15, 0))
        outcome = mgr.reschedule(primary_id, new_start, now=FUTURE_NOW)
        session.commit()

        # The primary is on Thursday, 15:00.
        assert outcome.appointment.start_time == new_start
        assert outcome.appointment.appointment_date == THURSDAY
        # The continuation is on Thursday, 15:30 — same row, moved along.
        assert outcome.continuation is not None
        assert outcome.continuation.id == cont_id
        assert outcome.continuation.start_time == datetime.combine(THURSDAY, time(15, 30))
        assert outcome.continuation.appointment_date == THURSDAY

        # And the partner row in the DB is consistent: same id, new day.
        repo = AppointmentRepository(session, tenant.id)
        primary = repo.get_by_id(primary_id)
        partner = repo.get_by_id(cont_id)
        assert primary is not None and primary.appointment_date == THURSDAY
        assert partner is not None and partner.appointment_date == THURSDAY

    def test_reschedule_missing_raises(self, session) -> None:
        tenant = _seed_tenant(session)
        mgr = AppointmentManageService(session, tenant.id)
        with pytest.raises(AppointmentNotFoundError):
            mgr.reschedule(uuid4(),
                           datetime.combine(WEDNESDAY, time(13, 0)),
                           now=FUTURE_NOW)

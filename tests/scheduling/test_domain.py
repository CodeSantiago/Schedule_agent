"""Tests for the pure-Python scheduling domain layer.

These tests do NOT touch the database. They exercise:

  - CB consumes 2 consecutive 30-min slots (and the second one is also marked
    as occupied so it cannot be double-booked)
  - haircut-only restriction is honoured for non-C services and ignored for C
  - past-time bookings on the same day are rejected
  - date-specific absences (whole day and partial) block their range
  - date-specific extra hours extend availability
  - already-booked appointments block the relevant slots (including the
    second half of a CB)

The domain layer is purely about behaviour; SQLAlchemy / FastAPI are tested
separately under `tests/repositories/` and `tests/api/`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from uuid import uuid4

import pytest

from packages.domain.scheduling import (
    AbsenceEntry,
    BookingError,
    BookingSlot,
    ExistingAppointment,
    ExtraHourEntry,
    PastTimeError,
    ScheduleEntry,
    ServiceCode,
    ServiceRestrictionError,
    SlotTakenError,
    TimeGrid,
    TimeRange,
    compute_available_slots,
    parse_haircut_only,
    plan_booking,
)
from packages.domain.scheduling.availability import AvailabilityQuery
from packages.domain.scheduling.booking import BookingRequest


# --- Common fixtures -------------------------------------------------------

WEDNESDAY = date(2026, 6, 24)  # 2026-06-24 is a Wednesday
SATURDAY = date(2026, 6, 27)


def _default_range() -> TimeRange:
    return TimeRange(time(10, 0), time(20, 0))


def _schedules(*ranges: TimeRange, weekday: str = "wed") -> tuple[ScheduleEntry, ...]:
    return tuple(ScheduleEntry(weekday=weekday, range=r) for r in ranges)


def _no_existing() -> tuple[ExistingAppointment, ...]:
    return ()


# --- Slot grid basics ------------------------------------------------------


class TestTimeGrid:
    def test_weekday_of(self) -> None:
        assert TimeGrid.weekday_of(WEDNESDAY) == "wed"
        assert TimeGrid.weekday_of(SATURDAY) == "sat"
        assert TimeGrid.weekday_of(date(2026, 6, 22)) == "mon"

    def test_slots_in_range_full_day(self) -> None:
        slots = TimeGrid.slots_in_range(time(10, 0), time(20, 0))
        assert slots[0] == time(10, 0)
        assert slots[-1] == time(19, 30)
        assert len(slots) == 20  # 10h * 2 slots/h

    def test_slots_in_range_rejects_misaligned(self) -> None:
        with pytest.raises(ValueError):
            TimeGrid.slots_in_range(time(10, 15), time(20, 0))

    def test_add_slots_basic(self) -> None:
        assert TimeGrid.add_slots(date(2026, 1, 1), time(10, 0), 1) == datetime(
            2026, 1, 1, 10, 30
        )
        assert TimeGrid.add_slots(date(2026, 1, 1), time(19, 30), 1) == datetime(
            2026, 1, 1, 20, 0
        )


# --- Haircut-only parsing --------------------------------------------------


class TestParseHaircutOnly:
    def test_none_or_empty(self) -> None:
        assert parse_haircut_only(None) == {}
        assert parse_haircut_only("") == {}

    def test_single_weekday(self) -> None:
        out = parse_haircut_only("mon:11:30,19:30")
        assert out == {"mon": [time(11, 30), time(19, 30)]}

    def test_multiple_weekdays(self) -> None:
        out = parse_haircut_only("mon:11:30,19:30;fri:15:00,19:00")
        assert out == {
            "mon": [time(11, 30), time(19, 30)],
            "fri": [time(15, 0), time(19, 0)],
        }

    def test_bad_weekday_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_haircut_only("xxx:11:30")

    def test_bad_time_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_haircut_only("mon:11:15")

    def test_misaligned_time_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_haircut_only("mon:10:15")


# --- Availability ---------------------------------------------------------


@dataclass
class _Fixture:
    barber_id: object
    restrictions: str | None = None


FIXTURE = _Fixture(barber_id=uuid4(), restrictions="wed:11:30,19:30")


def _query(
    *,
    service: ServiceCode = ServiceCode.HAIRCUT,
    target: date = WEDNESDAY,
    schedules: tuple[ScheduleEntry, ...] = _schedules(_default_range()),
    absences: tuple[AbsenceEntry, ...] = (),
    extra_hours: tuple[ExtraHourEntry, ...] = (),
    existing: tuple[ExistingAppointment, ...] = _no_existing(),
    restrictions: str | None = FIXTURE.restrictions,
    now: datetime | None = None,
) -> AvailabilityQuery:
    if now is None:
        now = datetime(2026, 6, 24, 0, 0)  # a day before any target date
    return AvailabilityQuery(
        barber_id=FIXTURE.barber_id,
        service=service,
        date_=target,
        schedules=schedules,
        absences=absences,
        extra_hours=extra_hours,
        existing_appointments=existing,
        restrictions=restrictions,
        now=now,
    )


class TestAvailabilityBasics:
    def test_no_schedule_returns_empty(self) -> None:
        out = compute_available_slots(_query(schedules=()))
        assert out == []

    def test_full_day_returns_all_slots(self) -> None:
        out = compute_available_slots(
            _query(schedules=_schedules(_default_range()))
        )
        # 10:00 .. 19:30 inclusive = 20 slots.
        assert len(out) == 20
        assert out[0].start_time == time(10, 0)
        assert out[-1].start_time == time(19, 30)

    def test_partial_range(self) -> None:
        out = compute_available_slots(
            _query(
                schedules=_schedules(TimeRange(time(14, 0), time(16, 0)))
            )
        )
        assert [s.start_time for s in out] == [time(14, 0), time(14, 30), time(15, 0), time(15, 30)]


class TestCbDoubleSlot:
    def test_cb_starts_need_followup_slot_free(self) -> None:
        # Last valid CB start must be 19:00 (so the second half is 19:30,
        # which fits inside [10:00, 20:00)). 19:30 cannot be a CB start.
        out = compute_available_slots(
            _query(
                service=ServiceCode.HAIRCUT_AND_BEARD,
                schedules=_schedules(_default_range()),
            )
        )
        starts = [s.start_time for s in out]
        assert time(19, 30) not in starts, "CB must not start at the last slot"
        # All starts are aligned to the half hour and end before 20:00.
        for s in out:
            assert s.start_time.minute in (0, 30)

    def test_cb_blocked_when_second_slot_taken(self) -> None:
        taken = ExistingAppointment(
            tenant_id=uuid4(),
            barber_id=FIXTURE.barber_id,
            date_=WEDNESDAY,
            start_time=time(10, 30),  # blocks the second half of a 10:00 CB
            n_slots=1,
        )
        out = compute_available_slots(
            _query(
                service=ServiceCode.HAIRCUT_AND_BEARD,
                schedules=_schedules(_default_range()),
                existing=(taken,),
            )
        )
        assert time(10, 0) not in [s.start_time for s in out]
        # But 10:30 itself is now taken for non-CB too.
        assert time(10, 30) not in [s.start_time for s in out]


class TestHaircutOnly:
    def test_haircut_allowed_at_restricted_slot(self) -> None:
        out = compute_available_slots(
            _query(
                service=ServiceCode.HAIRCUT,
                schedules=_schedules(_default_range()),
            )
        )
        assert time(11, 30) in [s.start_time for s in out]
        assert time(19, 30) in [s.start_time for s in out]

    def test_beard_blocked_at_restricted_slot(self) -> None:
        out = compute_available_slots(
            _query(
                service=ServiceCode.BEARD,
                schedules=_schedules(_default_range()),
            )
        )
        starts = [s.start_time for s in out]
        assert time(11, 30) not in starts
        assert time(19, 30) not in starts
        # Adjacent slots remain available.
        assert time(11, 0) in starts
        assert time(12, 0) in starts

    def test_haircut_and_beard_blocked_at_restricted_slot(self) -> None:
        out = compute_available_slots(
            _query(
                service=ServiceCode.HAIRCUT_AND_BEARD,
                schedules=_schedules(_default_range()),
            )
        )
        starts = [s.start_time for s in out]
        assert time(11, 30) not in starts
        assert time(19, 30) not in starts

    def test_no_restrictions_means_no_blocking(self) -> None:
        out = compute_available_slots(
            _query(
                service=ServiceCode.BEARD,
                schedules=_schedules(_default_range()),
                restrictions=None,
            )
        )
        assert time(11, 30) in [s.start_time for s in out]


class TestPastTimeRejection:
    def test_same_day_past_time_excluded(self) -> None:
        # The fixture schedules a full 10:00-20:00 day. If 'now' is
        # 2026-06-24 11:00, the next bookable slot is 11:30.
        now = datetime(2026, 6, 24, 11, 0)
        out = compute_available_slots(
            _query(
                target=WEDNESDAY,
                schedules=_schedules(_default_range()),
                now=now,
            )
        )
        starts = [s.start_time for s in out]
        assert time(10, 0) not in starts
        assert time(10, 30) not in starts
        assert time(11, 0) not in starts
        assert time(11, 30) in starts

    def test_exactly_at_now_still_excluded(self) -> None:
        # Past-time is "<=" — if the slot has started, it cannot be booked.
        now = datetime(2026, 6, 24, 11, 0)
        out = compute_available_slots(
            _query(
                target=WEDNESDAY,
                schedules=_schedules(_default_range()),
                now=now,
            )
        )
        assert time(11, 0) not in [s.start_time for s in out]

    def test_future_date_unaffected(self) -> None:
        now = datetime(2026, 6, 24, 11, 0)
        out = compute_available_slots(
            _query(
                target=SATURDAY,
                schedules=_schedules(_default_range(), weekday="sat"),
                now=now,
            )
        )
        assert time(10, 0) in [s.start_time for s in out]


class TestAbsences:
    def test_whole_day_absence_blocks_everything(self) -> None:
        out = compute_available_slots(
            _query(
                absences=(AbsenceEntry(absence_date=WEDNESDAY),),
            )
        )
        assert out == []

    def test_partial_absence_blocks_only_range(self) -> None:
        out = compute_available_slots(
            _query(
                absences=(
                    AbsenceEntry(
                        absence_date=WEDNESDAY,
                        start_time=time(12, 0),
                        end_time=time(14, 0),
                    ),
                ),
            )
        )
        starts = [s.start_time for s in out]
        # 12:00, 12:30, 13:00, 13:30 are blocked; 11:30 and 14:00 remain.
        assert time(11, 30) in starts
        assert time(12, 0) not in starts
        assert time(13, 30) not in starts
        assert time(14, 0) in starts

    def test_absence_only_applies_to_target_date(self) -> None:
        out = compute_available_slots(
            _query(
                target=SATURDAY,
                schedules=_schedules(_default_range(), weekday="sat"),
                absences=(AbsenceEntry(absence_date=WEDNESDAY),),
            )
        )
        assert len(out) == 20


class TestExtraHours:
    def test_extra_hours_extend_availability(self) -> None:
        out = compute_available_slots(
            _query(
                extra_hours=(
                    ExtraHourEntry(
                        extra_date=SATURDAY,
                        range=TimeRange(time(14, 0), time(16, 0)),
                    ),
                ),
                target=SATURDAY,
                schedules=_schedules(_default_range(), weekday="sat"),
            )
        )
        # Saturday default schedule is 10-20; extra 14-16 just adds nothing
        # new (already covered). Let's check it doesn't break anything:
        assert time(10, 0) in [s.start_time for s in out]
        assert time(15, 0) in [s.start_time for s in out]

    def test_extra_hours_outside_default_open_otherwise_closed(self) -> None:
        # Saturday has NO default schedule; only the extra hours.
        out = compute_available_slots(
            _query(
                target=SATURDAY,
                schedules=(),
                extra_hours=(
                    ExtraHourEntry(
                        extra_date=SATURDAY,
                        range=TimeRange(time(14, 0), time(15, 0)),
                    ),
                ),
            )
        )
        starts = [s.start_time for s in out]
        assert starts == [time(14, 0), time(14, 30)]


# --- Plan booking (intent to write) ---------------------------------------


class TestPlanBooking:
    OPEN_SCHEDULE = (
        ScheduleEntry(weekday="wed", range=TimeRange(time(10, 0), time(20, 0))),
    )

    def test_non_cb_books_single_slot(self) -> None:
        req = BookingRequest(
            tenant_id=uuid4(),
            barber_id=FIXTURE.barber_id,
            service=ServiceCode.HAIRCUT,
            date_=WEDNESDAY,
            start_time=datetime(2026, 6, 24, 11, 0),
            restrictions=FIXTURE.restrictions,
            schedules=self.OPEN_SCHEDULE,
            now=datetime(2026, 6, 24, 0, 0),
        )
        plan = plan_booking(req)
        assert plan.is_haircut_and_beard is False
        assert plan.primary.start_time == time(11, 0)
        assert plan.continuation is None
        assert plan.as_starts() == [time(11, 0)]

    def test_cb_books_two_consecutive_slots(self) -> None:
        req = BookingRequest(
            tenant_id=uuid4(),
            barber_id=FIXTURE.barber_id,
            service=ServiceCode.HAIRCUT_AND_BEARD,
            date_=WEDNESDAY,
            start_time=datetime(2026, 6, 24, 11, 0),
            restrictions=FIXTURE.restrictions,
            schedules=self.OPEN_SCHEDULE,
            now=datetime(2026, 6, 24, 0, 0),
        )
        plan = plan_booking(req)
        assert plan.is_haircut_and_beard is True
        assert plan.primary.start_time == time(11, 0)
        assert plan.continuation is not None
        assert plan.continuation.start_time == time(11, 30)
        assert plan.as_starts() == [time(11, 0), time(11, 30)]

    def test_past_time_same_day_raises(self) -> None:
        req = BookingRequest(
            tenant_id=uuid4(),
            barber_id=FIXTURE.barber_id,
            service=ServiceCode.HAIRCUT,
            date_=WEDNESDAY,
            start_time=datetime(2026, 6, 24, 11, 0),
            restrictions=FIXTURE.restrictions,
            schedules=self.OPEN_SCHEDULE,
            now=datetime(2026, 6, 24, 11, 30),  # 11:00 already started
        )
        with pytest.raises(PastTimeError):
            plan_booking(req)

    def test_haircut_only_rejects_beard_at_restricted_slot(self) -> None:
        req = BookingRequest(
            tenant_id=uuid4(),
            barber_id=FIXTURE.barber_id,
            service=ServiceCode.BEARD,
            date_=WEDNESDAY,
            start_time=datetime(2026, 6, 24, 11, 30),
            restrictions=FIXTURE.restrictions,
            schedules=self.OPEN_SCHEDULE,
            now=datetime(2026, 6, 24, 0, 0),
        )
        with pytest.raises(ServiceRestrictionError):
            plan_booking(req)

    def test_slot_taken_raises_when_first_half_occupied(self) -> None:
        other = uuid4()
        req = BookingRequest(
            tenant_id=uuid4(),
            barber_id=FIXTURE.barber_id,
            service=ServiceCode.HAIRCUT,
            date_=WEDNESDAY,
            start_time=datetime(2026, 6, 24, 11, 0),
            restrictions=FIXTURE.restrictions,
            schedules=self.OPEN_SCHEDULE,
            existing_appointments=(
                ExistingAppointment(
                    tenant_id=other,
                    barber_id=FIXTURE.barber_id,
                    date_=WEDNESDAY,
                    start_time=time(11, 0),
                    n_slots=1,
                ),
            ),
            now=datetime(2026, 6, 24, 0, 0),
        )
        with pytest.raises(SlotTakenError):
            plan_booking(req)

    def test_slot_taken_raises_when_cb_second_half_occupied(self) -> None:
        other = uuid4()
        req = BookingRequest(
            tenant_id=uuid4(),
            barber_id=FIXTURE.barber_id,
            service=ServiceCode.HAIRCUT_AND_BEARD,
            date_=WEDNESDAY,
            start_time=datetime(2026, 6, 24, 11, 0),
            restrictions=FIXTURE.restrictions,
            schedules=self.OPEN_SCHEDULE,
            existing_appointments=(
                ExistingAppointment(
                    tenant_id=other,
                    barber_id=FIXTURE.barber_id,
                    date_=WEDNESDAY,
                    start_time=time(11, 30),  # blocks CB's second half
                    n_slots=1,
                ),
            ),
            now=datetime(2026, 6, 24, 0, 0),
        )
        with pytest.raises(SlotTakenError):
            plan_booking(req)

    def test_misaligned_start_raises(self) -> None:
        req = BookingRequest(
            tenant_id=uuid4(),
            barber_id=FIXTURE.barber_id,
            service=ServiceCode.HAIRCUT,
            date_=WEDNESDAY,
            start_time=datetime(2026, 6, 24, 11, 15),
            restrictions=FIXTURE.restrictions,
            schedules=self.OPEN_SCHEDULE,
            now=datetime(2026, 6, 24, 0, 0),
        )
        with pytest.raises(ValueError):
            plan_booking(req)

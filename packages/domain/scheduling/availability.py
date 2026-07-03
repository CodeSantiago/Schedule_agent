"""Slot availability computation.

This is the single source of truth for "what slots can a customer book for
this barber on this date for this service?" — combining:

  - the barber's weekly schedule for that weekday
  - any date-specific extra hours
  - any date-specific absences
  - the service's slot footprint (HAIRCUT_AND_BEARD = 2, others = 1)
  - the haircut-only restriction per (weekday, slot_start)
  - already-booked appointments (so we don't return slots we can't take)
  - the "no past-time bookings" rule for same-day requests

The function is pure: same inputs → same output, no side effects.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Iterable

from packages.domain.scheduling.models import (
    AbsenceEntry,
    BookingSlot,
    ExistingAppointment,
    ExtraHourEntry,
    ScheduleEntry,
    ServiceCode,
    TimeGrid,
    TimeRange,
)
from packages.domain.scheduling.restrictions import enforce_haircut_only


@dataclass(frozen=True)
class AvailabilityQuery:
    """All inputs the availability function needs.

    Bundling them into a single object keeps the function signature stable as
    we add inputs (e.g. tenant time zone, barber-specific slot step) in
    future slices.
    """

    barber_id: object  # opaque to the domain
    service: ServiceCode
    date_: date
    schedules: tuple[ScheduleEntry, ...]  # weekly schedule entries (any weekday)
    absences: tuple[AbsenceEntry, ...]  # date-specific absences
    extra_hours: tuple[ExtraHourEntry, ...]  # date-specific extra hours
    existing_appointments: tuple[ExistingAppointment, ...]
    restrictions: str | None  # haircut-only restriction string
    now: datetime  # for past-time rejection
    tenant_id: object | None = None  # for cross-tenant safety in adapters


def _absences_for_date(
    absences: Iterable[AbsenceEntry], target: date
) -> list[AbsenceEntry]:
    return [a for a in absences if a.absence_date == target]


def _extras_for_date(
    extras: Iterable[ExtraHourEntry], target: date
) -> list[ExtraHourEntry]:
    return [e for e in extras if e.extra_date == target]


def _weekly_ranges_for(
    schedules: Iterable[ScheduleEntry], weekday: str
) -> list[TimeRange]:
    return [s.range for s in schedules if s.weekday == weekday]


def _occupied_starts(
    appointments: Iterable[ExistingAppointment],
    target: date,
    barber_id: object,
) -> set[tuple[time, int]]:
    """Return {(start_time, n_slots)} for appointments that block this barber on date."""
    out: set[tuple[time, int]] = set()
    for appt in appointments:
        if appt.date_ != target or appt.barber_id != barber_id:
            continue
        # Decompose the appointment into individual 30-min occupied starts.
        # This way, a HAIRCUT_AND_BEARD appointment contributes both its
        # start and the continuation, so neither slot can be double-booked.
        total_minutes = appt.start_time.hour * 60 + appt.start_time.minute
        for i in range(appt.n_slots):
            h, m = divmod(total_minutes + i * TimeGrid.SLOT_MINUTES, 60)
            out.add((time(h, m), 1))
    return out


def compute_available_slots(query: AvailabilityQuery) -> list[BookingSlot]:
    """Return the list of bookable starting slots for this query.

    The list is sorted by start time. A slot is returned only if the barber
    is open at the starting time AND every subsequent slot the service
    occupies is also open and free.
    """
    weekday = TimeGrid.weekday_of(query.date_)
    weekly = _weekly_ranges_for(query.schedules, weekday)
    extras = [e.range for e in _extras_for_date(query.extra_hours, query.date_)]
    ranges = weekly + extras

    if not ranges:
        return []

    absences_today = _absences_for_date(query.absences, query.date_)
    occupied = _occupied_starts(
        query.existing_appointments, query.date_, query.barber_id
    )

    # The minimum wall-clock start: if the date is today, never return
    # slots earlier than the current time (rounded UP to the next slot).
    earliest_today: time | None = None
    if query.date_ == query.now.date():
        cur = query.now.time()
        cur_min = cur.hour * 60 + cur.minute
        # Round up to the next slot start. If we're already on the grid, we
        # can still book the current slot as long as it has not started
        # yet (we use the slot's start time as the cutoff).
        cur_min = ((cur_min // TimeGrid.SLOT_MINUTES) + 1) * TimeGrid.SLOT_MINUTES
        h, m = divmod(cur_min, 60)
        if h < 24:
            earliest_today = time(h, m)

    out: list[BookingSlot] = []
    n_slots = query.service.default_slots

    # Collect all candidate starts across all ranges, sorted.
    candidate_starts: list[time] = []
    for rng in ranges:
        candidate_starts.extend(TimeGrid.slots_in_range(rng.start, rng.end))
    candidate_starts.sort()

    for start in candidate_starts:
        # Past-time guard (same-day only; future dates are unaffected).
        if earliest_today is not None and start < earliest_today:
            continue

        # Whole-day absence always blocks; partial absence blocks the range.
        blocked = False
        for ab in absences_today:
            if ab.is_whole_day() or ab.blocks(start):
                blocked = True
                break
        if blocked:
            continue

        # Haircut-only guard: at restricted (weekday, start), only HAIRCUT is
        # allowed. HAIRCUT_AND_BEARD cannot start there because its first half
        # would already be non-HAIRCUT.
        try:
            enforce_haircut_only(
                query.service, query.restrictions, weekday, start
            )
        except Exception:  # ServiceRestrictionError
            continue

        # All n_slots must fit within the open ranges AND be free.
        end = _add_slots_to_time(start, n_slots)
        if end is None:
            continue  # crosses midnight → can't book
        if not _all_within_ranges(start, end, ranges):
            continue
        if _any_blocked_by_appointment(start, n_slots, occupied):
            continue

        out.append(BookingSlot(date_=query.date_, start_time=start))

    return out


# --- Small helpers (kept here to avoid bloating models.py) -----------------


def _add_slots_to_time(start: time, n_slots: int) -> time | None:
    total = start.hour * 60 + start.minute + n_slots * TimeGrid.SLOT_MINUTES
    if total >= 24 * 60:
        return None
    h, m = divmod(total, 60)
    return time(h, m)


def _all_within_ranges(
    start: time, end: time, ranges: Iterable[TimeRange]
) -> bool:
    """True if `[start, end)` is fully inside at least one of the ranges."""
    for rng in ranges:
        if rng.start <= start and end <= rng.end:
            return True
    return False


def _any_blocked_by_appointment(
    start: time, n_slots: int, occupied: set[tuple[time, int]]
) -> bool:
    total = start.hour * 60 + start.minute
    for i in range(n_slots):
        h, m = divmod(total + i * TimeGrid.SLOT_MINUTES, 60)
        if (time(h, m), 1) in occupied:
            return True
    return False

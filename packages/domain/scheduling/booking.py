"""Booking planning.

The plan is the *intent* to occupy slots — produced by `plan_booking` and
then realised by the application layer (which writes one Appointment row
per occupied slot to the database). Keeping this in the pure domain layer
means we test the rule "HAIRCUT_AND_BEARD consumes 2 consecutive 30-minute slots" without
touching SQLAlchemy.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from packages.domain.scheduling.errors import (
    BarberUnavailableError,
    PastTimeError,
    SlotTakenError,
)
from packages.domain.scheduling.models import (
    AbsenceEntry,
    BookingPlan,
    BookingSlot,
    ExistingAppointment,
    ExtraHourEntry,
    ScheduleEntry,
    ServiceCode,
    TimeGrid,
)
from packages.domain.scheduling.restrictions import enforce_haircut_only


@dataclass(frozen=True)
class BookingRequest:
    """Inputs to `plan_booking`.

    The request carries everything the rule check needs: who, when, what.
    The application layer is responsible for fetching `existing_appointments`
    and translating the resulting plan into persistence operations.
    """

    tenant_id: object
    barber_id: object
    service: ServiceCode
    date_: date
    start_time: "datetime | None"  # wall-clock, tenant-local datetime; None = "any"
    duration_minutes: int | None = None  # override; defaults to service.default_slots * 30
    restrictions: str | None = None
    existing_appointments: tuple[ExistingAppointment, ...] = ()
    schedules: tuple[ScheduleEntry, ...] = ()  # weekly schedule for the weekday
    absences: tuple[AbsenceEntry, ...] = ()  # date-specific absences
    extra_hours: tuple[ExtraHourEntry, ...] = ()  # date-specific extra hours
    now: datetime | None = None  # for past-time rejection


def plan_booking(req: BookingRequest) -> BookingPlan:
    """Validate a booking request and return the concrete slots to occupy.

    Rules applied (in order):

    1. If `start_time` is given, it must be on the 30-minute grid and not in
       the past.
    2. The barber must be open at every slot the service occupies (weekly
       schedule ∪ extra hours). Absences block their range (whole day = all).
     3. Haircut-only restriction: at restricted (weekday, start), only HAIRCUT is allowed.
     4. HAIRCUT_AND_BEARD requires 2 consecutive 30-min slots. The continuation must
        be free (no existing appointment at the second half).
    5. Any other service: the single starting slot must be free.
    """
    if req.now is None:
        raise ValueError("BookingRequest.now is required for planning")

    n_slots = req.service.default_slots
    if req.duration_minutes is not None:
        if req.duration_minutes % TimeGrid.SLOT_MINUTES != 0:
            raise ValueError(
                f"duration_minutes must be a multiple of {TimeGrid.SLOT_MINUTES}"
            )
        n_slots = req.duration_minutes // TimeGrid.SLOT_MINUTES

    if req.start_time is None:
        raise ValueError(
            "plan_booking requires an explicit start_time; use "
            "compute_available_slots to find candidates first."
        )

    # Past-time guard: same-day bookings must reject slots that have already
    # started (legacy rule: "same day, past time → reject").
    if req.start_time.date() == req.now.date() and req.start_time <= req.now:
        raise PastTimeError(
            requested_iso=req.start_time.isoformat(),
            now_iso=req.now.isoformat(),
        )

    if not TimeGrid.is_aligned_to_grid(req.start_time.time()):
        raise ValueError(
            f"start_time {req.start_time.time()} is not on the 30-minute grid"
        )

    weekday = TimeGrid.weekday_of(req.start_time.date())
    enforce_haircut_only(req.service, req.restrictions, weekday, req.start_time.time())

    # --- Build the open/closed schedule for the requested date ------------

    weekly_ranges = [s.range for s in req.schedules if s.weekday == weekday]
    extra_ranges = [
        e.range for e in req.extra_hours if e.extra_date == req.start_time.date()
    ]
    open_ranges = tuple(weekly_ranges + extra_ranges)
    if not open_ranges:
        raise BarberUnavailableError(
            f"barber has no open range on {weekday}"
        )

    absences_today = [
        a for a in req.absences if a.absence_date == req.start_time.date()
    ]

    # --- Build the slot start times this booking will occupy --------------

    starts: list[datetime] = []
    base_total = req.start_time.hour * 60 + req.start_time.minute
    for i in range(n_slots):
        total = base_total + i * TimeGrid.SLOT_MINUTES
        if total >= 24 * 60:
            raise ValueError("booking crosses midnight; not supported")
        h, m = divmod(total, 60)
        starts.append(
            req.start_time.replace(hour=h, minute=m, second=0, microsecond=0)
        )

    # --- Every slot must be within an open range AND not blocked by absence
    for s in starts:
        t = s.time()
        in_range = any(rng.start <= t < rng.end for rng in open_ranges)
        if not in_range:
            raise BarberUnavailableError(
                f"barber is not open at {t} on {req.start_time.date()}"
            )
        for ab in absences_today:
            if ab.is_whole_day() or ab.blocks(t):
                raise BarberUnavailableError(
                    f"barber is on absence at {t} on {req.start_time.date()}"
                )

    # --- And every slot must be free of existing appointments -------------
    occupied: set[tuple] = set()
    for appt in req.existing_appointments:
        if appt.date_ != req.start_time.date() or appt.barber_id != req.barber_id:
            continue
        total = appt.start_time.hour * 60 + appt.start_time.minute
        for i in range(appt.n_slots):
            h, m = divmod(total + i * TimeGrid.SLOT_MINUTES, 60)
            occupied.add((h, m))

    for s in starts:
        if (s.hour, s.minute) in occupied:
            raise SlotTakenError(
                f"slot at {req.start_time.isoformat()} for barber {req.barber_id} is already taken"
            )

    primary = BookingSlot(date_=req.start_time.date(), start_time=req.start_time.time())
    continuation: BookingSlot | None = None
    if req.service.is_haircut_and_beard and len(starts) >= 2:
        continuation_time = starts[1].time()
        continuation = BookingSlot(date_=req.start_time.date(), start_time=continuation_time)

    return BookingPlan(
        service=req.service,
        date_=req.start_time.date(),
        primary=primary,
        continuation=continuation,
        occupied_starts=tuple(s.time() for s in starts),
    )

"""Domain value objects and the slot grid constants.

These are the only shapes the application layer must hand to the domain.
They are intentionally simple dataclasses (no behaviour beyond arithmetic)
so that adapters are trivial and tests are straightforward.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from enum import Enum
from typing import Iterable
from uuid import UUID

# --- Slot grid -------------------------------------------------------------

# Slot grid: business hours live in 30-minute slots starting on the hour or
# half-hour. CB (haircut and beard) consumes 2 consecutive slots.
SLOT_MINUTES = 30

# Exposed on `TimeGrid` too so callers can write `TimeGrid.SLOT_MINUTES`.
# (The constant lives at module scope for the test suite and the enum default.)

# Legacy working hours: 10:00 - 19:30 (last 30-min slot starts at 19:00).
# These are the business-day *defaults*; a barber with extra hours can extend.
DEFAULT_DAY_START = time(10, 0)
DEFAULT_DAY_END = time(20, 0)  # exclusive: last valid start is 19:30

# Weekday codes used throughout (ISO 8601, matching the `weekday` DB enum).
WEEKDAY_CODES: tuple[str, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


class TimeGrid:
    """Slot-grid arithmetic helpers (no IO, no SQL)."""

    SLOT_MINUTES: int = SLOT_MINUTES  # expose the constant on the class

    @staticmethod
    def weekday_of(d: date) -> str:
        """Return the ISO weekday code for a date (`mon`..`sun`)."""
        return WEEKDAY_CODES[d.weekday()]

    @staticmethod
    def is_aligned_to_grid(t: time) -> bool:
        """True when `t` is on the slot boundary (hh:00 or hh:30)."""
        return t.minute in (0, 30) and t.second == 0 and t.microsecond == 0

    @staticmethod
    def slots_in_range(start: time, end: time) -> list[time]:
        """Return the list of slot START times in `[start, end)` (exclusive end).

        The boundary is fixed to 30 minutes. If `end` is not on the grid, the
        last fully-contained slot is the one that starts at `end - 30min`.
        """
        if not TimeGrid.is_aligned_to_grid(start):
            raise ValueError(f"start {start} is not on the 30-minute grid")
        if not TimeGrid.is_aligned_to_grid(end):
            raise ValueError(f"end {end} is not on the 30-minute grid")
        out: list[time] = []
        cur_h, cur_m = start.hour, start.minute
        end_h, end_m = end.hour, end.minute
        while (cur_h, cur_m) < (end_h, end_m):
            out.append(time(cur_h, cur_m))
            cur_m += SLOT_MINUTES
            if cur_m >= 60:
                cur_h += 1
                cur_m -= 60
        return out

    @staticmethod
    def add_slots(d: date, t: time, n_slots: int) -> datetime:
        """Return the datetime that is `n_slots` 30-min slots after `(d, t)`."""
        if n_slots < 0:
            raise ValueError("n_slots must be >= 0")
        total_minutes = t.hour * 60 + t.minute + n_slots * SLOT_MINUTES
        h, m = divmod(total_minutes, 60)
        if h >= 24:
            raise ValueError("add_slots crosses midnight; not supported")
        return datetime(d.year, d.month, d.day, h, m)


# --- Service code ----------------------------------------------------------


class ServiceCode(str, Enum):
    """Service codes used by the legacy bot (C, B, CB) plus a generic OTHER.

    The codes are the canonical short identifiers the bot/UI uses in messages
    ("CB" = "Corte y Barba"). Domain logic keys off these codes, not IDs, so
    that hard-coded legacy rules (e.g. "CB consumes 2 slots") are easy to read.
    """

    HAIRCUT = "C"
    BEARD = "B"
    HAIRCUT_AND_BEARD = "CB"
    OTHER = "OTHER"

    @property
    def is_haircut_and_beard(self) -> bool:
        return self is ServiceCode.HAIRCUT_AND_BEARD

    @property
    def default_slots(self) -> int:
        """Number of 30-min slots this service consumes. CB = 2."""
        return 2 if self.is_haircut_and_beard else 1


# --- Time range & schedule inputs -----------------------------------------


@dataclass(frozen=True)
class TimeRange:
    """Half-open time range `[start, end)`, both on the slot grid."""

    start: time
    end: time

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise ValueError(f"end {self.end} must be > start {self.start}")
        if not TimeGrid.is_aligned_to_grid(self.start):
            raise ValueError(f"start {self.start} is not on the 30-minute grid")
        if not TimeGrid.is_aligned_to_grid(self.end):
            raise ValueError(f"end {self.end} is not on the 30-minute grid")

    def contains(self, t: time) -> bool:
        return self.start <= t < self.end

    def overlaps(self, other: "TimeRange") -> bool:
        return self.start < other.end and other.start < self.end


@dataclass(frozen=True)
class ScheduleEntry:
    """A weekly recurring availability entry for a barber."""

    weekday: str
    range: TimeRange

    def __post_init__(self) -> None:
        if self.weekday not in WEEKDAY_CODES:
            raise ValueError(f"unknown weekday {self.weekday!r}")


@dataclass(frozen=True)
class AbsenceEntry:
    """A date-specific absence. Both times None = whole day off."""

    absence_date: date
    start_time: time | None = None
    end_time: time | None = None

    def is_whole_day(self) -> bool:
        return self.start_time is None and self.end_time is None

    def blocks(self, t: time) -> bool:
        if self.is_whole_day():
            return True
        assert self.start_time is not None and self.end_time is not None
        return self.start_time <= t < self.end_time  # type: ignore[operator]


@dataclass(frozen=True)
class ExtraHourEntry:
    """A date-specific availability addition."""

    extra_date: date
    range: TimeRange


# --- Booking shapes --------------------------------------------------------


@dataclass(frozen=True)
class BookingSlot:
    """A starting 30-minute slot for a service on a specific date.

    `start_time` and `end_time` are wall-clock times (no timezone) in the
    tenant's local zone. The application layer is responsible for converting
    these into UTC datetimes when persisting.
    """

    date_: date
    start_time: time

    def end_time(self, n_slots: int = 1) -> time:
        if n_slots < 1:
            raise ValueError("n_slots must be >= 1")
        total = self.start_time.hour * 60 + self.start_time.minute + n_slots * SLOT_MINUTES
        h, m = divmod(total, 60)
        if h >= 24:
            raise ValueError("slot end crosses midnight; not supported")
        return time(h, m)


@dataclass(frozen=True)
class ExistingAppointment:
    """An appointment the domain must treat as already-booked.

    `n_slots` is the number of 30-min slots it occupies (CB = 2). The
    `barber_id` and `tenant_id` are kept so the application layer can match
    by ID; the domain itself never inspects them.
    """

    tenant_id: UUID
    barber_id: UUID
    date_: date
    start_time: time
    n_slots: int = 1


@dataclass(frozen=True)
class BookingPlan:
    """The output of `plan_booking`: the concrete slots that will be held.

    For non-HAIRCUT_AND_BEARD services this is always a single slot.
    For HAIRCUT_AND_BEARD it is two: the visible one (`primary`) and
    the continuation (`continuation`), which the application layer
    persists as a second appointment row tagged "(CB cont.)" to mirror
    the legacy sheets workflow.
    """

    service: ServiceCode
    date_: date
    primary: BookingSlot
    continuation: BookingSlot | None = None
    occupied_starts: tuple[time, ...] = field(default_factory=tuple)

    @property
    def is_haircut_and_beard(self) -> bool:
        return self.continuation is not None

    def as_starts(self) -> list[time]:
        return list(self.occupied_starts)

    def iter_slots(self) -> Iterable[BookingSlot]:
        yield self.primary
        if self.continuation is not None:
            yield self.continuation


# --- Per-barber availability set (used by the domain layer) ---------------


@dataclass(frozen=True)
class BarberAvailability:
    """All time ranges a barber is open on a specific date.

    `weekly_ranges` are the entries for that weekday from `barber_schedules`;
    `extra_ranges` are the extras for that date from `barber_extra_hours`.
    The domain simply unions them; the order of the union does not matter
    because `overlaps` is symmetric.
    """

    weekly_ranges: tuple[TimeRange, ...] = ()
    extra_ranges: tuple[TimeRange, ...] = ()

    def all_ranges(self) -> tuple[TimeRange, ...]:
        return self.weekly_ranges + self.extra_ranges

    def is_open_at(self, t: time) -> bool:
        return any(rng.contains(t) for rng in self.all_ranges())

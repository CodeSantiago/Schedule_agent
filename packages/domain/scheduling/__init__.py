"""Pure-Python scheduling domain logic.

This package contains **no SQLAlchemy / FastAPI / IO** — it operates on
plain dataclasses and primitives. The application layer adapts the
ORM rows to these shapes and the API layer calls the application
services. Keeping this layer pure means we can unit-test every legacy
business rule without spinning up a database.
"""

from packages.domain.scheduling.errors import (
    BarberUnavailableError,
    BookingError,
    DateClosedError,
    PastTimeError,
    ServiceRestrictionError,
    SlotTakenError,
    TenantMismatchError,
)
from packages.domain.scheduling.models import (
    BookingPlan,
    BookingSlot,
    ServiceCode,
    TimeGrid,
    TimeRange,
    BarberAvailability,
    ScheduleEntry,
    AbsenceEntry,
    ExtraHourEntry,
    ExistingAppointment,
)
from packages.domain.scheduling.restrictions import (
    parse_haircut_only,
    is_haircut_only_slot,
)
from packages.domain.scheduling.availability import (
    compute_available_slots,
)
from packages.domain.scheduling.booking import plan_booking
from packages.domain.scheduling.service_codes import parse_service_code

__all__ = [
    "AbsenceEntry",
    "BarberAvailability",
    "BarberUnavailableError",
    "BookingError",
    "BookingPlan",
    "BookingSlot",
    "DateClosedError",
    "ExistingAppointment",
    "ExtraHourEntry",
    "PastTimeError",
    "ScheduleEntry",
    "ServiceCode",
    "ServiceRestrictionError",
    "SlotTakenError",
    "TenantMismatchError",
    "TimeGrid",
    "TimeRange",
    "compute_available_slots",
    "is_haircut_only_slot",
    "parse_service_code",
    "parse_haircut_only",
    "plan_booking",
]

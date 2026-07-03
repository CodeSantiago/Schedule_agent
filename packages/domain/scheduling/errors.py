"""Domain-level errors for the scheduling slice.

These are caught at the application/API layer and translated to user-facing
messages. They are NOT SQLAlchemy errors and must not depend on infrastructure.
"""

from __future__ import annotations


class BookingError(ValueError):
    """Base class for all scheduling business-rule violations.

    The application layer is expected to translate each subclass to a clear
    user-facing message (and the API to the right HTTP status).
    """


class PastTimeError(BookingError):
    """The requested slot is in the past or starts before 'now'."""

    def __init__(self, requested_iso: str, now_iso: str) -> None:
        super().__init__(
            f"Cannot book a slot in the past: requested={requested_iso} now={now_iso}"
        )
        self.requested_iso = requested_iso
        self.now_iso = now_iso


class BarberUnavailableError(BookingError):
    """The barber is not scheduled to work at that slot (or is on absence)."""


class ServiceRestrictionError(BookingError):
    """The service is restricted at that slot (e.g. haircut-only for non-haircut)."""


class SlotTakenError(BookingError):
    """That slot is already taken (either by a confirmed appointment or by the
    second half of a CB booking)."""


class TenantMismatchError(BookingError):
    """Two IDs that should belong to the same tenant do not (data corruption or
    a cross-tenant write attempt)."""


class AppointmentNotFoundError(BookingError):
    """The appointment id does not exist (or belongs to a different tenant)."""


class AppointmentAlreadyCancelledError(BookingError):
    """Attempted to cancel an appointment that is already cancelled."""


class AppointmentInPastError(BookingError):
    """Attempted an operation (cancel/reschedule) on a past appointment."""


class AppointmentNotReschedulableError(BookingError):
    """The appointment cannot be rescheduled in its current state
    (e.g. it is already cancelled or completed)."""


class DateClosedError(BookingError):
    """The requested date is closed for booking (tenant holiday)."""

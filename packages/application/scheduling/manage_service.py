"""Appointment management service: cancel + reschedule.

Part 4 of the rebuild. The `BookingService` only knows how to create a
new booking; this service owns the rest of the lifecycle:

- `cancel(appointment_id)` — flips a row to `cancelled`. HAIRCUT_AND_BEARD
  primary rows cascade to the continuation row so the two halves stay
  in sync.
- `reschedule(appointment_id, new_start_at)` — moves a row to a new
  start time. Reuses the same `plan_booking` rules as a fresh booking
  (past-time, haircut-only, absence, double-booking) so the domain logic
  stays the single source of truth. HAIRCUT_AND_BEARD primary moves its
  continuation along; cancelling-and-rebooking would lose the customer
  data and is explicitly NOT what we do.

Both operations are tenant-scoped by construction (the service takes
`tenant_id` at construction time and uses `AppointmentRepository`,
which is bound to that tenant).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from uuid import UUID

from sqlalchemy.orm import Session

from packages.domain.scheduling import (
    AbsenceEntry,
    BookingError,
    DateClosedError,
    ExistingAppointment,
    ExtraHourEntry,
    ScheduleEntry,
    TimeRange,
    parse_service_code,
)
from packages.domain.scheduling.booking import BookingRequest, plan_booking
from packages.domain.scheduling.errors import (
    AppointmentAlreadyCancelledError,
    AppointmentInPastError,
    AppointmentNotFoundError,
    AppointmentNotReschedulableError,
)
from packages.infrastructure.db.models.appointments import Appointment
from packages.infrastructure.db.models.scheduling import Barber, Service
from packages.infrastructure.repositories import (
    AbsenceRepository,
    AppointmentRepository,
    BarberRepository,
    ExtraHourRepository,
    ScheduleRepository,
    ServiceRepository,
    TenantRepository,
)


@dataclass(frozen=True)
class CancelResult:
    """The outcome of a cancel. Both halves of a CB return a row."""

    cancelled: Appointment
    continuation_cancelled: Appointment | None = None


@dataclass(frozen=True)
class RescheduleResult:
    """The outcome of a reschedule. Both halves of a CB return a row."""

    appointment: Appointment
    continuation: Appointment | None = None


class AppointmentManageService:
    """Tenant-scoped appointment lifecycle operations.

    Stateless; the session is committed by the caller (the route).
    """

    def __init__(self, session: Session, tenant_id: UUID) -> None:
        self._session = session
        self._tenant_id = tenant_id
        self._appointments = AppointmentRepository(session, tenant_id)
        self._barbers = BarberRepository(session, tenant_id)
        self._services = ServiceRepository(session, tenant_id)
        self._schedules = ScheduleRepository(session, tenant_id)
        self._absences = AbsenceRepository(session, tenant_id)
        self._extras = ExtraHourRepository(session, tenant_id)

    @property
    def session(self) -> Session:
        return self._session

    # --- Cancel -----------------------------------------------------------

    def cancel(self, appointment_id: UUID, *, now: datetime | None = None) -> CancelResult:
        """Cancel a single appointment (and its CB partner if present).

        Rules:

        1. The appointment must exist for this tenant; otherwise
           `AppointmentNotFoundError`.
        2. It must not already be cancelled (`AppointmentAlreadyCancelledError`).
        3. It must not be in the past (`AppointmentInPastError`).
        4. HAIRCUT_AND_BEARD partners (the "(CB cont.)" row) are cancelled together so
           the unique slot guard does not block a future re-booking of
           the slot's primary half.
        """
        row = self._appointments.get_by_id(appointment_id)
        if row is None:
            raise AppointmentNotFoundError(
                f"appointment {appointment_id} not found for this tenant"
            )
        if row.status == "cancelled":
            raise AppointmentAlreadyCancelledError(
                f"appointment {appointment_id} is already cancelled"
            )

        effective_now = now or datetime.now()
        if self._is_in_past(row, effective_now):
            raise AppointmentInPastError(
                f"appointment {appointment_id} is in the past and cannot be cancelled"
            )

        row.status = "cancelled"
        self._session.flush()

        continuation: Appointment | None = None
        if self._is_haircut_and_beard_continuation(row):
            primary = self._appointments.find_cb_primary(
                barber_id=row.barber_id,
                appointment_date=row.appointment_date,
                start_time=row.start_time,
            )
            if primary is not None and primary.status != "cancelled":
                primary.status = "cancelled"
                self._session.flush()
        else:
            partner = self._appointments.find_cb_partner(
                barber_id=row.barber_id,
                appointment_date=row.appointment_date,
                start_time=row.start_time,
            )
            if partner is not None and partner.status != "cancelled":
                partner.status = "cancelled"
                self._session.flush()
                continuation = partner

        return CancelResult(cancelled=row, continuation_cancelled=continuation)

    # --- Reschedule -------------------------------------------------------

    def reschedule(
        self,
        appointment_id: UUID,
        new_start_at: datetime,
        *,
        now: datetime | None = None,
    ) -> RescheduleResult:
        """Move an appointment to a new start time.

        Reuses `plan_booking` so the same rules that gate a fresh
        booking (slot grid alignment, past-time, haircut-only, barber
        availability, double-booking) gate the reschedule. The original
        row is treated as "not taken" when we re-plan: the new slot
        must be free, but the existing row's slot is allowed to be free
        after the move.
        """
        row = self._appointments.get_by_id(appointment_id)
        if row is None:
            raise AppointmentNotFoundError(
                f"appointment {appointment_id} not found for this tenant"
            )
        if row.status == "cancelled":
            raise AppointmentNotReschedulableError(
                f"cancelled appointment {appointment_id} cannot be rescheduled"
            )
        if row.status == "completed":
            raise AppointmentNotReschedulableError(
                f"completed appointment {appointment_id} cannot be rescheduled"
            )

        effective_now = now or datetime.now()

        # Past-time guard applies only on the same day. A future-day
        # reschedule is allowed even if "now" is later than the original
        # start (we never want to block moving a stale appointment
        # forward).
        if new_start_at.date() == effective_now.date() and new_start_at <= effective_now:
            raise AppointmentInPastError(
                f"new start_at {new_start_at.isoformat()} is in the past"
            )

        # Check if the target date is closed for booking.
        _guard_closed_date(self._tenant_id, new_start_at.date(), self._session)

        barber = self._barbers.get_by_id(row.barber_id)
        if barber is None:
            raise BookingError(
                f"barber {row.barber_id} not found for this tenant"
            )
        service = self._services.get_by_id(row.service_id)
        if service is None:
            raise BookingError(
                f"service {row.service_id} not found for this tenant"
            )
        service_code = parse_service_code(service.code)

        weekday = _weekday_for(new_start_at.date())
        schedule_rows = self._schedules.list_for_barber_and_weekday(
            row.barber_id, weekday
        )
        absence_rows = self._absences.list_for_barber_on_date(
            row.barber_id, new_start_at.date()
        )
        extra_rows = self._extras.list_for_barber_on_date(
            row.barber_id, new_start_at.date()
        )
        existing_rows = self._appointments.get_for_barber_on(
            row.barber_id, new_start_at.date()
        )
        # Exclude the row we are rescheduling (and its CB partner) from
        # the "already taken" check — we are about to move them.
        existing_rows = [
            r for r in existing_rows
            if r.id != row.id
            and not self._is_linked_cb_partner(r, row)
        ]

        plan = plan_booking(
            BookingRequest(
                tenant_id=self._tenant_id,
                barber_id=row.barber_id,
                service=service_code,
                date_=new_start_at.date(),
                start_time=new_start_at,
                restrictions=barber.restrictions,
                existing_appointments=tuple(
                    ExistingAppointment(
                        tenant_id=r.tenant_id,
                        barber_id=r.barber_id,
                        date_=r.appointment_date,
                        start_time=r.start_time.time(),
                        n_slots=1,
                    )
                    for r in existing_rows
                ),
                schedules=tuple(
                    ScheduleEntry(weekday=r.weekday, range=TimeRange(r.start_time, r.end_time))
                    for r in schedule_rows
                ),
                absences=tuple(
                    AbsenceEntry(
                        absence_date=r.absence_date,
                        start_time=r.start_time,
                        end_time=r.end_time,
                    )
                    for r in absence_rows
                ),
                extra_hours=tuple(
                    ExtraHourEntry(extra_date=r.extra_date, range=TimeRange(r.start_time, r.end_time))
                    for r in extra_rows
                ),
                now=effective_now,
            )
        )

        # Apply the plan: mutate the original row + (optionally) its CB
        # partner. The unique constraint will block the move if the new
        # slot collides with another tenant row (or with the same row
        # when the new start matches the old start and start_time is
        # exactly the same — but that is the caller's bug to send).
        old_start = row.start_time
        old_date = row.appointment_date
        new_primary_start = datetime.combine(new_start_at.date(), plan.primary.start_time)
        row.appointment_date = new_start_at.date()
        row.start_time = new_primary_start
        row.end_time = new_primary_start + timedelta(minutes=30)
        # status stays whatever it was (pending/confirmed). The webhook
        # / bot may need to confirm the new time with the customer; that
        # is a Part 4+ responsibility.
        self._session.flush()

        continuation: Appointment | None = None
        if plan.is_haircut_and_beard and plan.continuation is not None:
            cont_start = datetime.combine(new_start_at.date(), plan.continuation.start_time)
            cont_end = cont_start + timedelta(minutes=30)
            # Look up the partner on the OLD date — `row.appointment_date`
            # was already updated above, so a query against it would miss
            # the partner when the reschedule crosses a day boundary.
            partner = self._appointments.find_cb_partner(
                barber_id=row.barber_id,
                appointment_date=old_date,
                start_time=old_start,
            )
            if partner is not None:
                partner.appointment_date = new_start_at.date()
                partner.start_time = cont_start
                partner.end_time = cont_end
                self._session.flush()
                continuation = partner

        return RescheduleResult(appointment=row, continuation=continuation)

    # --- Internal helpers -------------------------------------------------

    @staticmethod
    def _is_haircut_and_beard_continuation(row: Appointment) -> bool:
        return "(CB cont.)" in (row.customer_name or "")

    def _is_linked_cb_partner(self, candidate: Appointment, anchor: Appointment) -> bool:
        """True if `candidate` is the CB partner of `anchor`.

        Used during reschedule: the existing-rows list must not include
        the partner that travels with the row being moved.
        """
        if candidate.id == anchor.id:
            return False
        if candidate.barber_id != anchor.barber_id:
            return False
        if candidate.appointment_date != anchor.appointment_date:
            return False
        anchor_is_cont = self._is_haircut_and_beard_continuation(anchor)
        cand_is_cont = self._is_haircut_and_beard_continuation(candidate)
        if anchor_is_cont == cand_is_cont:
            return False
        delta = abs((candidate.start_time - anchor.start_time).total_seconds())
        return delta == 30 * 60

    @staticmethod
    def _is_in_past(row: Appointment, now: datetime) -> bool:
        """True when the appointment is on the same day AND the start
        time has already passed.

        We deliberately allow cancel/reschedule on past-day appointments
        because the operator regularly marks no-shows or late cancels
        after the fact. The "you can't book a slot that already started
        today" rule is a booking concern, not a lifecycle concern.
        """
        if row.start_time.date() != now.date():
            return False
        return row.start_time <= now


def _guard_closed_date(tenant_id: UUID, target_date: date, session: Session) -> None:
    """Raise ``DateClosedError`` when the tenant has closed the target date."""
    tenant_repo = TenantRepository(session, tenant_id)
    settings = tenant_repo.get_settings()
    if settings is None:
        return
    raw = settings.config or {}
    closed_dates: list = (raw.get("booking", {}) or {}).get("closed_dates", [])
    target_str = target_date.isoformat()
    if target_str in closed_dates:
        raise DateClosedError(
            f"tenant {tenant_id} has closed {target_str} for booking"
        )


def _weekday_for(d: date) -> str:
    return ("mon", "tue", "wed", "thu", "fri", "sat", "sun")[d.weekday()]

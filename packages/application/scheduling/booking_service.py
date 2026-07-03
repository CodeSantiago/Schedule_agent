"""Booking application service.

Owns the read-then-write sequence that turns a "customer wants slot X
for service Y with barber Z on date D" into one or two persisted
appointment rows. The business rules (HAIRCUT_AND_BEARD occupies 2 slots,
haircut-only, past-time, absences, extra hours) all live in the domain
layer; this service is just the glue.

HAIRCUT_AND_BEARD appointments are persisted as TWO appointment rows
(the primary and its continuation) so the legacy `(CB cont.)` semantic
survives in the new system without a separate table. The unique
constraint on `(tenant_id, barber_id, date, start_time)` is the source
of truth for "is this slot already taken" — even under a race, the
second insert will fail loudly and the caller retries.

How the service code is decided
-------------------------------

The service type question is NOT inferred from `duration_minutes` — that
field drives price/duration display only. The booking service reads the
canonical short code from the `services.code` column (e.g. `"C"`, `"B"`,
`"CB"`, or the long `"CORTE"`, `"BARBA"`, `"CORTE_Y_BARBA"`) and maps it
to a `ServiceCode` enum via the shared `parse_service_code` helper in the
domain layer. The slot count then comes from `ServiceCode.default_slots`
(HAIRCUT_AND_BEARD = 2, everything else = 1).

`duration_minutes` is consulted ONLY when an explicit override is
passed on the `BookingRequest`; otherwise the service-code default
wins. Availability uses the same parser, so a tenant that enters
`"CORTE_Y_BARBA"` on the service row is classified identically in
both endpoints — avoiding the haircut-only filtering drift that the
Part 2 verification flagged.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from uuid import UUID

from sqlalchemy.orm import Session

from packages.domain.scheduling import (
    AbsenceEntry,
    BookingError,
    BookingPlan,
    DateClosedError,
    ExistingAppointment,
    ExtraHourEntry,
    ScheduleEntry,
    TimeRange,
    parse_service_code,
)
from packages.domain.scheduling.booking import BookingRequest, plan_booking
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
class BookSlotCommand:
    """Everything the booking service needs to attempt a booking."""

    tenant_id: UUID
    barber_id: UUID
    service_id: UUID
    start_at: datetime  # tenant-local wall-clock datetime
    customer_name: str
    customer_phone: str
    # Configurable identity fields (optional, driven by tenant identity_mode)
    customer_last_name: str | None = None
    customer_dni: str | None = None
    notes: str | None = None
    # If True, skip sheets write-back (used for test/import bookings).
    skip_sheets_sync: bool = False
    # 'now' is the moment the booking is attempted. Defaults to
    # datetime.now(UTC) in the service. Callers that need deterministic
    # tests can pass a fixed value.
    now: datetime | None = None


@dataclass(frozen=True)
class BookSlotResult:
    """Result of a successful booking.

    For non-CB services `appointment` is the single row; `continuation`
    is None. For CB, BOTH rows are returned (the second one is the
    "(CB cont.)" slot the legacy bot also used to block).
    """

    appointment: Appointment
    continuation: Appointment | None


class BookingService:
    """Orchestrates: load → plan → persist. Tenant-scoped by construction."""

    def __init__(self, session: Session, tenant_id: UUID) -> None:
        self._session = session
        self._tenant_id = tenant_id
        self._barbers = BarberRepository(session, tenant_id)
        self._services = ServiceRepository(session, tenant_id)
        self._schedules = ScheduleRepository(session, tenant_id)
        self._absences = AbsenceRepository(session, tenant_id)
        self._extras = ExtraHourRepository(session, tenant_id)
        self._appointments = AppointmentRepository(session, tenant_id)

    @property
    def tenant_id(self) -> UUID:
        return self._tenant_id

    @property
    def session(self) -> Session:
        """Expose the underlying session for the API layer to commit."""
        return self._session

    # --- Read paths -------------------------------------------------------

    def list_barbers(self) -> list[Barber]:
        return self._barbers.list()

    def list_active_barbers(self) -> list[Barber]:
        return self._barbers.list_active()

    def list_services(self) -> list[Service]:
        return self._services.list()

    # --- Write paths ------------------------------------------------------

    def book_slot(self, cmd: BookSlotCommand) -> BookSlotResult:
        """Plan + persist a single booking, optionally syncing to sheets.

        Raises ``BookingError`` subclasses on rule violations; the caller
        is expected to map them to the right HTTP response.
        """
        return self._book_slot_impl(cmd)

    def _book_slot_impl(self, cmd: BookSlotCommand) -> BookSlotResult:
        """Plan + persist a single booking.

        Raises `BookingError` subclasses on rule violations; the caller
        is expected to map them to the right HTTP response.
        """
        barber = self._barbers.get_by_id(cmd.barber_id)
        if barber is None:
            raise BookingError(f"barber {cmd.barber_id} not found for this tenant")
        service = self._services.get_by_id(cmd.service_id)
        if service is None:
            raise BookingError(f"service {cmd.service_id} not found for this tenant")

        # Check if the requested date is closed for booking.
        _guard_closed_date(self._tenant_id, cmd.start_at.date(), self._session)

        # Resolve the service code from the `services.code` column via the
        # shared domain parser. Accepts "C" / "B" / "CB" (short legacy
        # form) and the long forms "CORTE" / "BARBA" / "CORTE_Y_BARBA".
        # Unknown values fall back to OTHER (treated like a single-slot
        # Corte) so new services don't break the booking flow.
        service_code = parse_service_code(service.code)

        weekday = _weekday_for(cmd.start_at.date())
        schedules_rows = self._schedules.list_for_barber_and_weekday(
            cmd.barber_id, weekday
        )
        absences_rows = self._absences.list_for_barber_on_date(
            cmd.barber_id, cmd.start_at.date()
        )
        extras_rows = self._extras.list_for_barber_on_date(
            cmd.barber_id, cmd.start_at.date()
        )
        existing_rows = self._appointments.get_for_barber_on(
            cmd.barber_id, cmd.start_at.date()
        )

        schedule_entries = tuple(
            ScheduleEntry(weekday=r.weekday, range=TimeRange(r.start_time, r.end_time))
            for r in schedules_rows
        )
        absence_entries = tuple(
            AbsenceEntry(
                absence_date=r.absence_date,
                start_time=r.start_time,
                end_time=r.end_time,
            )
            for r in absences_rows
        )
        extra_entries = tuple(
            ExtraHourEntry(
                extra_date=r.extra_date, range=TimeRange(r.start_time, r.end_time)
            )
            for r in extras_rows
        )
        existing_entries = tuple(
            ExistingAppointment(
                tenant_id=r.tenant_id,
                barber_id=r.barber_id,
                date_=r.appointment_date,
                start_time=r.start_time.time(),
                n_slots=1,
            )
            for r in existing_rows
        )

        plan = plan_booking(
            BookingRequest(
                tenant_id=cmd.tenant_id,
                barber_id=cmd.barber_id,
                service=service_code,
                date_=cmd.start_at.date(),
                start_time=cmd.start_at,
                restrictions=barber.restrictions,
                existing_appointments=existing_entries,
                schedules=schedule_entries,
                absences=absence_entries,
                extra_hours=extra_entries,
                now=cmd.now or datetime.now(),
            )
        )

        # Format the display name based on tenant identity mode.
        formatted_name = self._format_customer_name(cmd)

        return self._persist_plan(cmd, plan, service, formatted_name)

    def _format_customer_name(self, cmd: BookSlotCommand) -> str:
        """Format ``customer_name`` based on tenant identity mode."""
        try:
            from packages.application.tenant.mode_service import TenantModeService

            mode_svc = TenantModeService(self._session, cmd.tenant_id)
            return mode_svc.format_customer_name(
                cmd.customer_name,
                cmd.customer_last_name,
                cmd.customer_dni,
            )
        except Exception:
            logger.exception("[booking] identity formatting failed, using raw name")
            return cmd.customer_name

    def _persist_plan(
        self, cmd: BookSlotCommand, plan: BookingPlan, service: Service,
        formatted_name: str | None = None,
    ) -> BookSlotResult:
        # Each persisted row is a 30-minute slot. HAIRCUT_AND_BEARD writes
        # two rows; non-HAIRCUT_AND_BEARD writes one. The primary row
        # carries the customer data;
        # the continuation row is tagged "(CB cont.)" so any downstream
        # reader (UI, reports, cancel flow) can tell the halves apart.
        display_name = formatted_name or cmd.customer_name
        primary_start = datetime.combine(cmd.start_at.date(), plan.primary.start_time)
        primary_end = primary_start + timedelta(minutes=30)
        primary = Appointment(
            tenant_id=cmd.tenant_id,
            barber_id=cmd.barber_id,
            service_id=cmd.service_id,
            appointment_date=cmd.start_at.date(),
            start_time=primary_start,
            end_time=primary_end,
            status="pending",
            customer_name=display_name,
            customer_phone=cmd.customer_phone,
            customer_dni=cmd.customer_dni,
            customer_last_name=cmd.customer_last_name,
            notes=cmd.notes,
        )
        self._session.add(primary)
        self._session.flush()

        continuation: Appointment | None = None
        if plan.continuation is not None and plan.is_haircut_and_beard:
            cont_start = datetime.combine(
                cmd.start_at.date(), plan.continuation.start_time
            )
            cont_end = cont_start + timedelta(minutes=30)
            continuation = Appointment(
                tenant_id=cmd.tenant_id,
                barber_id=cmd.barber_id,
                service_id=cmd.service_id,
                appointment_date=cmd.start_at.date(),
                start_time=cont_start,
                end_time=cont_end,
                status="pending",
                customer_name=f"{display_name} (CB cont.)",
                customer_phone=cmd.customer_phone,
                customer_dni=cmd.customer_dni,
                customer_last_name=cmd.customer_last_name,
                notes=cmd.notes,
            )
            self._session.add(continuation)
            self._session.flush()

        # Sheets write-back (best-effort after DB commit).
        if not cmd.skip_sheets_sync:
            self._sync_to_sheets(cmd, display_name, primary, service)

        return BookSlotResult(appointment=primary, continuation=continuation)

    def _sync_to_sheets(
        self,
        cmd: BookSlotCommand,
        display_name: str,
        appointment: Appointment,
        service: Service,
    ) -> None:
        """Best-effort append appointment to Google Sheets."""
        try:
            from packages.application.tenant.mode_service import TenantModeService

            mode_svc = TenantModeService(self._session, cmd.tenant_id)
            writer = mode_svc.get_sheets_writer()
            if writer is None or not writer.is_writeable:
                return

            barber_obj = self._barbers.get_by_id(cmd.barber_id)
            barber_name = barber_obj.name if barber_obj else str(cmd.barber_id)

            writer.append_appointment(
                appointment_date=cmd.start_at.date(),
                start_time=appointment.start_time,
                barber_name=barber_name,
                customer_name=display_name,
                service_name=service.name,
                status=appointment.status,
                customer_phone=cmd.customer_phone,
                customer_dni=cmd.customer_dni,
                notes=cmd.notes,
            )
        except Exception:
            logger.exception(
                "[booking] sheets write-back failed — booking already committed"
            )


def _guard_closed_date(tenant_id: UUID, target_date: date, session: Session) -> None:
    """Raise ``DateClosedError`` when the tenant has closed the target date."""
    from datetime import date as _date

    tenant_repo = TenantRepository(session, tenant_id)
    settings = tenant_repo.get_settings()
    if settings is None:
        return
    raw = settings.config or {}
    closed_dates: list = (raw.get("booking", {}) or {}).get("closed_dates", [])
    target_str = target_date.isoformat()  # YYYY-MM-DD
    if target_str in closed_dates:
        raise DateClosedError(
            f"tenant {tenant_id} has closed {target_str} for booking"
        )


def _weekday_for(d: date) -> str:
    return ("mon", "tue", "wed", "thu", "fri", "sat", "sun")[d.weekday()]


"""Availability endpoint (per-tenant, per-barber, per-date, per-service).

Returns the list of bookable starting slots for that combination, after
applying weekly schedule, extra hours, absences, haircut-only and
past-time rules. The heavy lifting lives in the domain layer.

The service code is parsed via the shared `parse_service_code` helper
in the domain layer so availability and booking classify a service
identically — a tenant that stores `"CORTE_Y_BARBA"` on the row is
treated as a CB by BOTH endpoints, not as OTHER in one and CB in the
other. (See the Part 2 verification note on haircut-only filtering
drift.)
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from apps.api.src.deps import (
    TenantPath,
    get_absence_repo,
    get_barber_repo,
    get_extra_hour_repo,
    get_schedule_repo,
    get_service_repo,
)
from apps.api.src.schemas import AvailabilityOut, AvailabilitySlot
from packages.domain.scheduling import (
    AbsenceEntry,
    ExtraHourEntry,
    ScheduleEntry,
    TimeRange,
    compute_available_slots,
    parse_service_code,
)
from packages.domain.scheduling.availability import AvailabilityQuery
from packages.infrastructure.repositories import (
    AbsenceRepository,
    BarberRepository,
    ExtraHourRepository,
    ScheduleRepository,
    ServiceRepository,
)

router = APIRouter(
    prefix="/tenants/{tenant_id}/availability",
    tags=["availability"],
)


@router.get("", response_model=AvailabilityOut)
def get_availability(
    tenant_id: TenantPath,
    barber_id: UUID,
    service_id: UUID,
    date: date,
    barber_repo: BarberRepository = Depends(get_barber_repo),
    service_repo: ServiceRepository = Depends(get_service_repo),
    schedule_repo: ScheduleRepository = Depends(get_schedule_repo),
    absence_repo: AbsenceRepository = Depends(get_absence_repo),
    extra_hour_repo: ExtraHourRepository = Depends(get_extra_hour_repo),
) -> AvailabilityOut:
    barber = barber_repo.get_by_id(barber_id)
    if barber is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"barber {barber_id} not found for this tenant",
        )
    service = service_repo.get_by_id(service_id)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"service {service_id} not found for this tenant",
        )

    weekday = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")[date.weekday()]
    schedules = tuple(
        ScheduleEntry(weekday=r.weekday, range=TimeRange(r.start_time, r.end_time))
        for r in schedule_repo.list_for_barber_and_weekday(barber_id, weekday)
    )
    absences = tuple(
        AbsenceEntry(
            absence_date=r.absence_date,
            start_time=r.start_time,
            end_time=r.end_time,
        )
        for r in absence_repo.list_for_barber_on_date(barber_id, date)
    )
    extras = tuple(
        ExtraHourEntry(
            extra_date=r.extra_date, range=TimeRange(r.start_time, r.end_time)
        )
        for r in extra_hour_repo.list_for_barber_on_date(barber_id, date)
    )

    # Resolve the service code once so the query and the per-slot
    # `end_time` calculation agree, and so availability matches what
    # the booking endpoint would do for the same service row.
    service_code = parse_service_code(service.code)

    # For availability, we treat "no existing appointments" — the customer
    # is asking what COULD they book, not what's already taken by them.
    query = AvailabilityQuery(
        barber_id=barber_id,
        service=service_code,
        date_=date,
        schedules=schedules,
        absences=absences,
        extra_hours=extras,
        existing_appointments=(),
        restrictions=barber.restrictions,
        now=datetime.now(),
    )
    slots = compute_available_slots(query)

    return AvailabilityOut(
        barber_id=barber_id,
        service_id=service_id,
        date=date,
        slots=[
            AvailabilitySlot(
                date=slot.date_,
                start_time=slot.start_time,
                end_time=slot.end_time(service_code.default_slots),
            )
            for slot in slots
        ],
    )

"""Appointment endpoints (per-tenant).

POST creates a booking (1 or 2 rows depending on service code).
GET lists appointments for a barber on a date or in a range.
DELETE / {id} cancels an appointment (and its CB partner).
POST / {id}/reschedule moves an appointment to a new start time.
PATCH / {id}/status updates an appointment's status.
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from apps.api.src.deps import (
    get_appointment_repo,
    get_booking_service,
    get_manage_service,
    require_tenant,
)
from apps.api.src.schemas import (
    AppointmentCreate,
    AppointmentOut,
    BookingResult,
    CancelOut,
    RescheduleOut,
    RescheduleRequest,
    StatusUpdateRequest,
)
from packages.application.scheduling.booking_service import (
    BookSlotCommand,
    BookingService,
)
from packages.application.scheduling.manage_service import (
    AppointmentManageService,
    CancelResult,
    RescheduleResult,
)
from packages.domain.scheduling.errors import (
    AppointmentAlreadyCancelledError,
    AppointmentInPastError,
    AppointmentNotFoundError,
    AppointmentNotReschedulableError,
    BookingError,
    DateClosedError,
    PastTimeError,
    ServiceRestrictionError,
    SlotTakenError,
    TenantMismatchError,
)
from packages.infrastructure.repositories import (
    AppointmentRepository,
    TenantAuditLogRepository,
)

router = APIRouter(
    prefix="/tenants/{tenant_id}/appointments",
    tags=["appointments"],
)


@router.get("", response_model=list[AppointmentOut])
def list_appointments(
    barber_id: UUID | None = Query(
        default=None,
        description="Filter by barber (omit for all barbers in the tenant).",
    ),
    date_from: date | None = Query(
        default=None,
        description="Start date (inclusive).",
    ),
    date_to: date | None = Query(
        default=None,
        description="End date (inclusive).",
    ),
    repo: AppointmentRepository = Depends(get_appointment_repo),
    tenant_id: UUID = Depends(require_tenant),
) -> list[AppointmentOut]:
    if barber_id is not None:
        # Filter by barber + date range / single date.
        if date_from is not None and date_to is not None:
            rows = repo.get_for_barber_in_range(barber_id, date_from, date_to)
        elif date_from is not None:
            rows = repo.get_for_barber_in_range(barber_id, date_from, date_from)
        elif date_to is not None:
            rows = repo.get_for_barber_in_range(barber_id, date_to, date_to)
        else:
            return []
    else:
        # All barbers — use tenant-scoped methods.
        if date_from is not None and date_to is not None:
            rows = repo.list_for_tenant_in_range(date_from, date_to)
        elif date_from is not None:
            rows = repo.list_for_tenant_on(date_from)
        elif date_to is not None:
            rows = repo.list_for_tenant_on(date_to)
        else:
            return []
    return [AppointmentOut.model_validate(r) for r in rows]


@router.post(
    "",
    response_model=BookingResult,
    status_code=status.HTTP_201_CREATED,
)
def create_appointment(
    payload: AppointmentCreate,
    service: BookingService = Depends(get_booking_service),
    tenant_id: UUID = Depends(require_tenant),
) -> BookingResult:
    try:
        result = service.book_slot(
            BookSlotCommand(
                tenant_id=tenant_id,
                barber_id=payload.barber_id,
                service_id=payload.service_id,
                start_at=payload.start_at,
                customer_name=payload.customer_name,
                customer_phone=payload.customer_phone,
                customer_last_name=payload.customer_last_name,
                customer_dni=payload.customer_dni,
                notes=payload.notes,
            )
        )
    except BookingError as exc:
        # Map domain errors to the right HTTP status. Past-time and
        # restriction errors are user-correctable -> 422. Slot-taken
        # and tenant-mismatch are conflicts -> 409 / 404.
        if isinstance(exc, DateClosedError):
            # Log the closed-date rejection.
            audit = TenantAuditLogRepository(service.session, tenant_id)
            audit.log(
                event_type="booking_closed_date_rejected",
                level="warn",
                message=f"Booking rejected — date is closed: {payload.start_at.date()}",
                details={
                    "barber_id": str(payload.barber_id),
                    "requested_date": payload.start_at.date().isoformat(),
                },
            )
            service.session.commit()
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            )
        if isinstance(exc, (PastTimeError, ServiceRestrictionError)):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            )
        if isinstance(exc, SlotTakenError):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=str(exc)
            )
        if isinstance(exc, TenantMismatchError):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        )

    service.session.commit()
    return BookingResult(
        appointment=AppointmentOut.model_validate(result.appointment),
        continuation=(
            AppointmentOut.model_validate(result.continuation)
            if result.continuation is not None
            else None
        ),
    )


@router.delete(
    "/{appointment_id}",
    response_model=CancelOut,
    status_code=status.HTTP_200_OK,
)
def cancel_appointment(
    appointment_id: UUID,
    svc: AppointmentManageService = Depends(get_manage_service),
    tenant_id: UUID = Depends(require_tenant),
) -> CancelOut:
    """Cancel one appointment (and its CB partner if any).

    Returns the cancelled primary row + the cancelled partner (if any)
    so the caller can show the user the full effect of the action.
    """
    try:
        result: CancelResult = svc.cancel(appointment_id)
    except AppointmentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except AppointmentAlreadyCancelledError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except AppointmentInPastError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    svc.session.commit()
    return CancelOut(
        appointment=AppointmentOut.model_validate(result.cancelled),
        continuation=(
            AppointmentOut.model_validate(result.continuation_cancelled)
            if result.continuation_cancelled is not None
            else None
        ),
    )


@router.post(
    "/{appointment_id}/reschedule",
    response_model=RescheduleOut,
    status_code=status.HTTP_200_OK,
)
def reschedule_appointment(
    appointment_id: UUID,
    payload: RescheduleRequest,
    svc: AppointmentManageService = Depends(get_manage_service),
    tenant_id: UUID = Depends(require_tenant),
) -> RescheduleOut:
    """Move an appointment to a new start time.

    The new slot is gated by the same domain rules as a fresh booking
    (slot grid, past-time, haircut-only, barber availability,
    double-booking). CB primary moves its continuation along.
    """
    try:
        result: RescheduleResult = svc.reschedule(
            appointment_id, payload.new_start_at
        )
    except AppointmentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except AppointmentNotReschedulableError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except AppointmentInPastError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except SlotTakenError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except (PastTimeError, ServiceRestrictionError, DateClosedError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except BookingError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    svc.session.commit()
    return RescheduleOut(
        appointment=AppointmentOut.model_validate(result.appointment),
        continuation=(
            AppointmentOut.model_validate(result.continuation)
            if result.continuation is not None
            else None
        ),
    )


@router.patch(
    "/{appointment_id}/status",
    response_model=AppointmentOut,
    status_code=status.HTTP_200_OK,
)
def update_appointment_status(
    appointment_id: UUID,
    payload: StatusUpdateRequest,
    repo: AppointmentRepository = Depends(get_appointment_repo),
    tenant_id: UUID = Depends(require_tenant),
) -> AppointmentOut:
    """Update an appointment's status.

    Valid statuses: pending, confirmed, cancelled, completed, no_show.
    Status transitions are enforced by the DB enum at the storage layer.
    """
    updated = repo.set_status(appointment_id, payload.status)
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"appointment {appointment_id} not found for this tenant",
        )
    repo.session.commit()
    repo.session.refresh(updated)
    return AppointmentOut.model_validate(updated)

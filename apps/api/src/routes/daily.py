"""Daily appointments endpoint (per-tenant).

Returns the list of appointments for a given date — a single call
that pulls together the data the legacy spreadsheet exposed to staff
("who has which customer when").
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends

from apps.api.src.deps import TenantPath, get_appointment_repo
from apps.api.src.schemas import AppointmentOut
from packages.infrastructure.repositories import AppointmentRepository

router = APIRouter(
    prefix="/tenants/{tenant_id}/daily",
    tags=["daily"],
)


@router.get("", response_model=list[AppointmentOut])
def get_daily_appointments(
    tenant_id: TenantPath,
    date: date,
    barber_id: UUID | None = None,
    repo: AppointmentRepository = Depends(get_appointment_repo),
) -> list[AppointmentOut]:
    """Return appointments for the given date, optionally filtered by barber.

    When `barber_id` is omitted, the result covers every barber in the
    tenant. The repository scopes the query by tenant, so other tenants'
    appointments never leak into the response.
    """
    if barber_id is not None:
        rows = repo.get_for_barber_in_range(barber_id, date, date)
    else:
        # No barber filter: scan the tenant's appointments for that date.
        # This stays scoped by `tenant_id` via the repo's WHERE clause.
        from sqlalchemy import select
        from packages.infrastructure.db.models.appointments import Appointment

        stmt = (
            select(Appointment)
            .where(Appointment.tenant_id == tenant_id)
            .where(Appointment.appointment_date == date)
            .where(Appointment.status != "cancelled")
            .order_by(Appointment.start_time)
        )
        rows = list(repo.session.execute(stmt).scalars())
    return [AppointmentOut.model_validate(r) for r in rows]

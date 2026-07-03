"""Barber workspace API routes.

Provides the data needed by the mobile-friendly barber workspace:
- Today's appointments for a specific barber
- Summary counts

These routes require a valid tenant-scoped bearer token and the barber
must belong to the tenant.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from apps.api.src.deps import (
    get_session,
    get_tenant_principal,
    require_tenant,
)
from packages.application.tenant.barber_workspace import (
    BarberWorkspaceService,
    WorkspaceAppointment,
    WorkspaceBarberInfo,
)

router = APIRouter(
    prefix="/tenants/{tenant_id}/workspace",
    tags=["workspace"],
)


class BarberInfoOut(BaseModel):
    id: str
    name: str
    is_active: bool


class AppointmentOut(BaseModel):
    id: str
    customer_name: str
    customer_phone: str
    service_name: str
    service_duration: int
    start_time: str
    end_time: str
    status: str
    notes: str | None = None


class BarberTodayOut(BaseModel):
    barber: BarberInfoOut
    target_date: str
    appointments: list[AppointmentOut]
    total: int
    pending: int
    confirmed: int
    completed: int
    cancelled: int


@router.get("/today", response_model=BarberTodayOut)
def barber_today(
    barber_id: UUID = Query(..., description="Barber UUID"),
    target_date: date | None = Query(
        default=None,
        alias="date",
        description="Date (defaults to today).",
    ),
    tenant_id: UUID = Depends(require_tenant),
    session: Session = Depends(get_session),
    _principal=Depends(get_tenant_principal),
) -> BarberTodayOut:
    """Return today's agenda for a specific barber.

    Requires a valid tenant-scoped bearer token. The barber must belong
    to this tenant.
    """
    svc = BarberWorkspaceService(session, tenant_id)
    try:
        result = svc.build_today(barber_id, target_date)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    return BarberTodayOut(
        barber=BarberInfoOut(
            id=result.barber.id,
            name=result.barber.name,
            is_active=result.barber.is_active,
        ),
        target_date=result.target_date,
        appointments=[
            AppointmentOut(
                id=a.id,
                customer_name=a.customer_name,
                customer_phone=a.customer_phone,
                service_name=a.service_name,
                service_duration=a.service_duration,
                start_time=a.start_time,
                end_time=a.end_time,
                status=a.status,
                notes=a.notes,
            )
            for a in result.appointments
        ],
        total=result.total,
        pending=result.pending,
        confirmed=result.confirmed,
        completed=result.completed,
        cancelled=result.cancelled,
    )

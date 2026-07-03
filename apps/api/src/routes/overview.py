"""Operational overview endpoint (per-tenant).

Drives the dashboard's KPI cards + the day's appointment list. The
service is in `packages.application.scheduling.overview_service`; this
module only adapts the dataclasses to pydantic for the response.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from apps.api.src.deps import get_overview_service, require_tenant
from apps.api.src.schemas import (
    OverviewCountsOut,
    OverviewDayAppointmentOut,
    OverviewOut,
)
from packages.application.scheduling.overview_service import OverviewService

router = APIRouter(
    prefix="/tenants/{tenant_id}/overview",
    tags=["overview"],
)


@router.get("", response_model=OverviewOut)
def get_overview(
    target_date: date = Query(
        default=None,
        alias="date",
        description="Day to summarise. Defaults to today (server-side).",
    ),
    svc: OverviewService = Depends(get_overview_service),
    tenant_id: UUID = Depends(require_tenant),
) -> OverviewOut:
    from datetime import date as _date

    if target_date is None:
        target_date = _date.today()
    result = svc.build(target_date)
    return OverviewOut(
        tenant_id=result.tenant_id,
        date=result.target_date,
        counts=OverviewCountsOut(
            booked_today=result.counts.booked_today,
            cancelled_today=result.counts.cancelled_today,
            completed_today=result.counts.completed_today,
            pending_today=result.counts.pending_today,
            confirmed_today=result.counts.confirmed_today,
            active_barbers=result.counts.active_barbers,
            active_services=result.counts.active_services,
            upcoming_days_with_bookings=result.counts.upcoming_days_with_bookings,
        ),
        appointments=[
            OverviewDayAppointmentOut(
                id=a.id,
                barber_name=a.barber_name,
                service_name=a.service_name,
                customer_name=a.customer_name,
                customer_phone=a.customer_phone,
                start_time=a.start_time,
                end_time=a.end_time,
                status=a.status,
                is_cb_continuation=a.is_cb_continuation,
            )
            for a in result.appointments
        ],
        upcoming=result.upcoming,
    )

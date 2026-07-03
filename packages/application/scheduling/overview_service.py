"""Operational overview service.

Drives the "tenant dashboard" cards. Today the cards are simple counts
and the day's appointment list. The service is intentionally thin: a
few read paths over the existing repos, no new business rules. The
shape of the result is the API contract the dashboard template binds
against, so it is the only place that defines the response schema.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from uuid import UUID

from sqlalchemy.orm import Session

from packages.infrastructure.repositories import (
    AppointmentRepository,
    BarberRepository,
    ServiceRepository,
)


@dataclass(frozen=True)
class OverviewCounts:
    """The KPI cards on the top of the dashboard."""

    booked_today: int = 0
    cancelled_today: int = 0
    completed_today: int = 0
    pending_today: int = 0
    confirmed_today: int = 0
    active_barbers: int = 0
    active_services: int = 0
    # Total count of distinct days with at least one appointment in the
    # next 7 days (rough "tomorrow is busy" hint).
    upcoming_days_with_bookings: int = 0


@dataclass(frozen=True)
class OverviewDayAppointment:
    """One row in the "today" list on the dashboard."""

    id: str
    barber_name: str
    service_name: str
    customer_name: str
    customer_phone: str
    start_time: str  # ISO datetime string
    end_time: str
    status: str
    is_cb_continuation: bool


@dataclass(frozen=True)
class OverviewResult:
    """Full overview payload returned to the dashboard."""

    tenant_id: str
    target_date: str  # ISO date
    counts: OverviewCounts
    appointments: list[OverviewDayAppointment] = field(default_factory=list)
    # Day-bucketed counts for the next 7 days (key = ISO date).
    upcoming: dict[str, int] = field(default_factory=dict)


class OverviewService:
    """Build the operational overview for one tenant on one day.

    Stateless; the session is committed by the caller.
    """

    def __init__(self, session: Session, tenant_id: UUID) -> None:
        self._session = session
        self._tenant_id = tenant_id
        self._appointments = AppointmentRepository(session, tenant_id)
        self._barbers = BarberRepository(session, tenant_id)
        self._services = ServiceRepository(session, tenant_id)

    @property
    def session(self) -> Session:
        return self._session

    def build(self, target_date: date) -> OverviewResult:
        # Status counts for the day.
        status_counts = self._appointments.count_status_for_day(target_date)

        # Barber / service active counts.
        active_barbers = sum(1 for b in self._barbers.list() if b.is_active)
        active_services = sum(1 for s in self._services.list() if s.is_active)

        # Day's appointments (active only — cancelled rows are counted
        # in the KPI card but not shown in the list).
        rows = self._appointments.list_for_tenant_on(target_date, include_cancelled=False)
        barber_by_id = {b.id: b for b in self._barbers.list()}
        service_by_id = {s.id: s for s in self._services.list()}

        appt_views: list[OverviewDayAppointment] = []
        for r in rows:
            barber = barber_by_id.get(r.barber_id)
            service = service_by_id.get(r.service_id)
            appt_views.append(
                OverviewDayAppointment(
                    id=str(r.id),
                    barber_name=barber.name if barber else "(deleted)",
                    service_name=service.name if service else "(deleted)",
                    customer_name=r.customer_name,
                    customer_phone=r.customer_phone,
                    start_time=r.start_time.isoformat(),
                    end_time=r.end_time.isoformat(),
                    status=r.status,
                    is_cb_continuation="(CB cont.)" in (r.customer_name or ""),
                )
            )

        # Upcoming 7-day bucket counts (active only).
        today = target_date
        upcoming: dict[str, int] = {}
        for offset in range(1, 8):
            d = today + timedelta(days=offset)
            n = len(self._appointments.list_for_tenant_on(d, include_cancelled=False))
            upcoming[d.isoformat()] = n

        counts = OverviewCounts(
            booked_today=sum(
                v for k, v in status_counts.items() if k != "cancelled"
            ),
            cancelled_today=status_counts.get("cancelled", 0),
            completed_today=status_counts.get("completed", 0),
            pending_today=status_counts.get("pending", 0),
            confirmed_today=status_counts.get("confirmed", 0),
            active_barbers=active_barbers,
            active_services=active_services,
            upcoming_days_with_bookings=sum(1 for v in upcoming.values() if v > 0),
        )

        return OverviewResult(
            tenant_id=str(self._tenant_id),
            target_date=target_date.isoformat(),
            counts=counts,
            appointments=appt_views,
            upcoming=upcoming,
        )

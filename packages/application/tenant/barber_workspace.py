"""Barber workspace service — today's agenda for a specific barber.

Provides the data needed by the barber mobile workspace:
- Today's appointments for a specific barber
- Summary counts (total, pending, confirmed, completed)
- Barber active/inactive status
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from uuid import UUID

from sqlalchemy.orm import Session

from packages.infrastructure.repositories import (
    AppointmentRepository,
    BarberRepository,
    ServiceRepository,
)
from packages.infrastructure.repositories.barbers import BarberRepository as BarberRepo
from packages.infrastructure.repositories.appointments import (
    AppointmentRepository as AppointmentRepo,
)
from packages.infrastructure.repositories.services import (
    ServiceRepository as ServiceRepo,
)


@dataclass(frozen=True)
class WorkspaceBarberInfo:
    id: str
    name: str
    is_active: bool


@dataclass(frozen=True)
class WorkspaceAppointment:
    id: str
    customer_name: str
    customer_phone: str
    service_name: str
    service_duration: int
    start_time: str
    end_time: str
    status: str
    notes: str | None = None


@dataclass(frozen=True)
class BarberTodayResult:
    barber: WorkspaceBarberInfo
    target_date: str
    appointments: list[WorkspaceAppointment] = field(default_factory=list)
    total: int = 0
    pending: int = 0
    confirmed: int = 0
    completed: int = 0
    cancelled: int = 0


class BarberWorkspaceService:
    """Build the barber's today view.

    Stateless; the session lifecycle is managed by the caller.
    """

    def __init__(self, session: Session, tenant_id: UUID) -> None:
        self._session = session
        self._tenant_id = tenant_id
        self._appointments = AppointmentRepo(session, tenant_id)
        self._barbers = BarberRepo(session, tenant_id)
        self._services = ServiceRepo(session, tenant_id)

    def build_today(
        self,
        barber_id: UUID,
        target_date: date | None = None,
    ) -> BarberTodayResult:
        """Build the today view for a single barber.

        Args:
            barber_id: The barber UUID.
            target_date: Optional date (defaults to today).

        Returns:
            A ``BarberTodayResult`` with appointments and counts.

        Raises:
            ValueError: If the barber does not exist for this tenant.
        """
        if target_date is None:
            from datetime import date as _date
            target_date = _date.today()

        barber = self._barbers.get_by_id(barber_id)
        if barber is None:
            raise ValueError(f"barber {barber_id} not found for tenant")

        services_by_id = {s.id: s for s in self._services.list()}

        # Fetch today's appointments for this barber
        rows = self._appointments.get_for_barber_in_range(
            barber_id, target_date, target_date
        )

        appointments: list[WorkspaceAppointment] = []
        counts = {"pending": 0, "confirmed": 0, "completed": 0, "cancelled": 0}

        for r in rows:
            svc = services_by_id.get(r.service_id)
            svc_name = svc.name if svc else "(unknown)"
            svc_dur = svc.duration_minutes if svc else 0

            appointments.append(
                WorkspaceAppointment(
                    id=str(r.id),
                    customer_name=r.customer_name,
                    customer_phone=r.customer_phone,
                    service_name=svc_name,
                    service_duration=svc_dur,
                    start_time=r.start_time.isoformat(),
                    end_time=r.end_time.isoformat(),
                    status=r.status,
                    notes=r.notes,
                )
            )
            if r.status in counts:
                counts[r.status] += 1  # type: ignore[literal-required]

        total_active = sum(v for k, v in counts.items() if k != "cancelled")

        return BarberTodayResult(
            barber=WorkspaceBarberInfo(
                id=str(barber.id),
                name=barber.name,
                is_active=barber.is_active,
            ),
            target_date=target_date.isoformat(),
            appointments=appointments,
            total=total_active,
            pending=counts["pending"],
            confirmed=counts["confirmed"],
            completed=counts["completed"],
            cancelled=counts["cancelled"],
        )

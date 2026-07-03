"""Appointment repository.

Bookings always go through the application service, which calls this
repo. The repo enforces tenant scope on every read and write so the
service cannot accidentally cross tenants.
"""

from __future__ import annotations

from datetime import date, time

from sqlalchemy import and_, func, select

from packages.infrastructure.db.models.appointments import Appointment
from packages.infrastructure.repositories.base import TenantScopedRepository


class AppointmentRepository(TenantScopedRepository[Appointment]):
    model = Appointment

    # --- Read paths --------------------------------------------------------

    def get_for_barber_on(
        self, barber_id, target: date
    ) -> list[Appointment]:
        """Active (non-cancelled) appointments for one barber on one date."""
        stmt = (
            self._by_tenant_stmt()
            .where(Appointment.barber_id == barber_id)
            .where(Appointment.appointment_date == target)
            .where(Appointment.status != "cancelled")
        )
        return list(self._session.execute(stmt).scalars())

    def get_for_barber_in_range(
        self,
        barber_id,
        date_from: date,
        date_to: date,
    ) -> list[Appointment]:
        stmt = (
            self._by_tenant_stmt()
            .where(Appointment.barber_id == barber_id)
            .where(
                and_(
                    Appointment.appointment_date >= date_from,
                    Appointment.appointment_date <= date_to,
                )
            )
            .where(Appointment.status != "cancelled")
        )
        return list(self._session.execute(stmt).scalars())

    def is_slot_taken(
        self,
        barber_id,
        target: date,
        start_time: time,
    ) -> bool:
        """True if there is an active appointment at the exact (barber, date, start)."""
        stmt = (
            self._by_tenant_stmt()
            .where(Appointment.barber_id == barber_id)
            .where(Appointment.appointment_date == target)
            .where(Appointment.start_time == start_time)
            .where(Appointment.status != "cancelled")
        )
        return self._session.execute(stmt).first() is not None

    def list_for_tenant_on(
        self, target: date, *, include_cancelled: bool = False
    ) -> list[Appointment]:
        """All appointments in this tenant on a given date (across barbers)."""
        stmt = (
            self._by_tenant_stmt()
            .where(Appointment.appointment_date == target)
            .order_by(Appointment.start_time)
        )
        if not include_cancelled:
            stmt = stmt.where(Appointment.status != "cancelled")
        return list(self._session.execute(stmt).scalars())

    def list_for_tenant_in_range(
        self, date_from: date, date_to: date, *, include_cancelled: bool = False
    ) -> list[Appointment]:
        stmt = (
            self._by_tenant_stmt()
            .where(
                and_(
                    Appointment.appointment_date >= date_from,
                    Appointment.appointment_date <= date_to,
                )
            )
            .order_by(Appointment.appointment_date, Appointment.start_time)
        )
        if not include_cancelled:
            stmt = stmt.where(Appointment.status != "cancelled")
        return list(self._session.execute(stmt).scalars())

    def find_cb_partner(
        self,
        *,
        barber_id,
        appointment_date: date,
        start_time,
    ) -> Appointment | None:
        """For a CB primary at `(barber, date, start_time)`, return the
        matching continuation row (the second 30-min half). The match
        is heuristic: same barber, same date, start_time exactly 30
        minutes later, and the customer_name carries the "(CB cont.)"
        tag the booking service sets.

        Returns None if no partner is found.
        """
        from datetime import datetime, timedelta

        next_start = (
            start_time
            if isinstance(start_time, datetime)
            else datetime.combine(appointment_date, start_time)
        ) + timedelta(minutes=30)
        stmt = (
            self._by_tenant_stmt()
            .where(Appointment.barber_id == barber_id)
            .where(Appointment.appointment_date == appointment_date)
            .where(Appointment.start_time == next_start)
            .where(Appointment.customer_name.like("%(CB cont.)%"))
        )
        return self._session.execute(stmt).scalar_one_or_none()

    def find_cb_primary(
        self,
        *,
        barber_id,
        appointment_date: date,
        start_time,
    ) -> Appointment | None:
        """Inverse of `find_cb_partner`: given a row tagged "(CB cont.)",
        return the matching primary row 30 minutes earlier on the same
        barber+date. Returns None if no primary is found.
        """
        from datetime import datetime, timedelta

        prev_start = (
            start_time
            if isinstance(start_time, datetime)
            else datetime.combine(appointment_date, start_time)
        ) - timedelta(minutes=30)
        stmt = (
            self._by_tenant_stmt()
            .where(Appointment.barber_id == barber_id)
            .where(Appointment.appointment_date == appointment_date)
            .where(Appointment.start_time == prev_start)
            .where(Appointment.customer_name.notlike("%(CB cont.)%"))
        )
        return self._session.execute(stmt).scalar_one_or_none()

    # --- Counts (for the dashboard overview) -------------------------------

    def count_status_for_day(self, target: date) -> dict[str, int]:
        """Return counts of appointments on a date grouped by status.

        All statuses present in the table are included (with `0` when no
        rows). Cancelled rows ARE counted so the dashboard can show
        "today: 12 booked, 3 cancelled".
        """
        out: dict[str, int] = {}
        stmt = (
            select(Appointment.status, func.count(Appointment.id))
            .where(Appointment.tenant_id == self._tenant_id)
            .where(Appointment.appointment_date == target)
            .group_by(Appointment.status)
        )
        for status_val, count in self._session.execute(stmt).all():
            out[status_val] = int(count)
        return out

    # --- Write paths -------------------------------------------------------

    def set_status(
        self, appointment_id, new_status: str
    ) -> Appointment | None:
        """Flip an appointment's status (e.g. 'cancelled', 'confirmed').

        Returns the updated row, or None if no row matched the tenant
        + id pair. Status changes do NOT free the slot on their own:
        `manage_service.cancel` handles the cleanup semantics.
        """
        row = self.get_by_id(appointment_id)
        if row is None:
            return None
        row.status = new_status
        self._session.flush()
        return row

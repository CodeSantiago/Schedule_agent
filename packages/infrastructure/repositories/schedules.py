"""Repositories for the scheduling layer: weekly schedules, absences, extra hours.

All three are read-mostly for the booking flow and write-only via the
admin / dashboard surface. They share the standard tenant scope from
`TenantScopedRepository` and add date-keyed queries the application
service needs.

The `tenant_id` column is NOT on these tables directly — it lives on
`barbers`. We enforce tenant scope on writes by checking that the
related `barber_id` belongs to this tenant before touching the row.
Reads always join through `barbers.tenant_id`.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select

from packages.domain.scheduling.errors import TenantMismatchError
from packages.infrastructure.db.models.scheduling import (
    Barber,
    BarberAbsence,
    BarberExtraHour,
    BarberSchedule,
)
from packages.infrastructure.repositories.base import TenantScopedRepository


def _barber_belongs_to(session, tenant_id: UUID, barber_id: Any) -> bool:
    """True if `barber_id` is a barber in `tenant_id`."""
    stmt = select(Barber.id).where(
        Barber.id == barber_id, Barber.tenant_id == tenant_id
    )
    return session.execute(stmt).first() is not None


class ScheduleRepository(TenantScopedRepository[BarberSchedule]):
    """Weekly recurring availability per barber."""

    model = BarberSchedule
    _tenant_column = "barber_id"  # not a real column on this table

    def _by_tenant_stmt(self):  # type: ignore[override]
        return (
            select(BarberSchedule)
            .join(Barber, BarberSchedule.barber_id == Barber.id)
            .where(Barber.tenant_id == self._tenant_id)
        )

    def add(self, row: BarberSchedule) -> BarberSchedule:  # type: ignore[override]
        if not _barber_belongs_to(self._session, self._tenant_id, row.barber_id):
            raise TenantMismatchError(
                f"barber {row.barber_id} does not belong to tenant {self._tenant_id}"
            )
        self._session.add(row)
        return row

    def delete(self, row_id: UUID) -> bool:  # type: ignore[override]
        # Join through barber to make sure we only delete within the tenant.
        stmt = (
            delete(BarberSchedule)
            .where(BarberSchedule.id == row_id)
            .where(
                BarberSchedule.barber_id.in_(
                    select(Barber.id).where(Barber.tenant_id == self._tenant_id)
                )
            )
        )
        return bool(self._session.execute(stmt).rowcount)

    def list_for_barber(self, barber_id) -> list[BarberSchedule]:
        stmt = self._by_tenant_stmt().where(BarberSchedule.barber_id == barber_id)
        return list(self._session.execute(stmt).scalars())

    def list_for_barber_and_weekday(
        self, barber_id, weekday: str
    ) -> list[BarberSchedule]:
        stmt = (
            self._by_tenant_stmt()
            .where(BarberSchedule.barber_id == barber_id)
            .where(BarberSchedule.weekday == weekday)
        )
        return list(self._session.execute(stmt).scalars())


class AbsenceRepository(TenantScopedRepository[BarberAbsence]):
    """Date-specific absences."""

    model = BarberAbsence
    _tenant_column = "barber_id"

    def _by_tenant_stmt(self):  # type: ignore[override]
        return (
            select(BarberAbsence)
            .join(Barber, BarberAbsence.barber_id == Barber.id)
            .where(Barber.tenant_id == self._tenant_id)
        )

    def add(self, row: BarberAbsence) -> BarberAbsence:  # type: ignore[override]
        if not _barber_belongs_to(self._session, self._tenant_id, row.barber_id):
            raise TenantMismatchError(
                f"barber {row.barber_id} does not belong to tenant {self._tenant_id}"
            )
        self._session.add(row)
        return row

    def delete(self, row_id: UUID) -> bool:  # type: ignore[override]
        stmt = (
            delete(BarberAbsence)
            .where(BarberAbsence.id == row_id)
            .where(
                BarberAbsence.barber_id.in_(
                    select(Barber.id).where(Barber.tenant_id == self._tenant_id)
                )
            )
        )
        return bool(self._session.execute(stmt).rowcount)

    def list_for_barber_on_date(
        self, barber_id, target: date
    ) -> list[BarberAbsence]:
        stmt = (
            self._by_tenant_stmt()
            .where(BarberAbsence.barber_id == barber_id)
            .where(BarberAbsence.absence_date == target)
        )
        return list(self._session.execute(stmt).scalars())

    def list_for_barber(
        self, barber_id, *, date_from: date | None = None, date_to: date | None = None
    ) -> list[BarberAbsence]:
        stmt = self._by_tenant_stmt().where(BarberAbsence.barber_id == barber_id)
        if date_from is not None:
            stmt = stmt.where(BarberAbsence.absence_date >= date_from)
        if date_to is not None:
            stmt = stmt.where(BarberAbsence.absence_date <= date_to)
        return list(self._session.execute(stmt).scalars())


class ExtraHourRepository(TenantScopedRepository[BarberExtraHour]):
    """Date-specific extra hours."""

    model = BarberExtraHour
    _tenant_column = "barber_id"

    def _by_tenant_stmt(self):  # type: ignore[override]
        return (
            select(BarberExtraHour)
            .join(Barber, BarberExtraHour.barber_id == Barber.id)
            .where(Barber.tenant_id == self._tenant_id)
        )

    def add(self, row: BarberExtraHour) -> BarberExtraHour:  # type: ignore[override]
        if not _barber_belongs_to(self._session, self._tenant_id, row.barber_id):
            raise TenantMismatchError(
                f"barber {row.barber_id} does not belong to tenant {self._tenant_id}"
            )
        self._session.add(row)
        return row

    def delete(self, row_id: UUID) -> bool:  # type: ignore[override]
        stmt = (
            delete(BarberExtraHour)
            .where(BarberExtraHour.id == row_id)
            .where(
                BarberExtraHour.barber_id.in_(
                    select(Barber.id).where(Barber.tenant_id == self._tenant_id)
                )
            )
        )
        return bool(self._session.execute(stmt).rowcount)

    def list_for_barber_on_date(
        self, barber_id, target: date
    ) -> list[BarberExtraHour]:
        stmt = (
            self._by_tenant_stmt()
            .where(BarberExtraHour.barber_id == barber_id)
            .where(BarberExtraHour.extra_date == target)
        )
        return list(self._session.execute(stmt).scalars())

    def list_for_barber(
        self, barber_id, *, date_from: date | None = None, date_to: date | None = None
    ) -> list[BarberExtraHour]:
        stmt = self._by_tenant_stmt().where(BarberExtraHour.barber_id == barber_id)
        if date_from is not None:
            stmt = stmt.where(BarberExtraHour.extra_date >= date_from)
        if date_to is not None:
            stmt = stmt.where(BarberExtraHour.extra_date <= date_to)
        return list(self._session.execute(stmt).scalars())

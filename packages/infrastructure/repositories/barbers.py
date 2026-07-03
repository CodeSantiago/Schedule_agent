"""Barber repository."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.infrastructure.db.models.scheduling import Barber
from packages.infrastructure.repositories.base import TenantScopedRepository


class BarberRepository(TenantScopedRepository[Barber]):
    model = Barber

    def get_by_id(self, barber_id: UUID) -> Barber | None:  # type: ignore[override]
        # `Barber.id` is the PK; same shape as the base implementation.
        return super().get_by_id(barber_id)

    def list_active(self) -> list[Barber]:
        stmt = self._by_tenant_stmt().where(Barber.is_active.is_(True))
        return list(self._session.execute(stmt).scalars())

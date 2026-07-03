"""Service repository.

The "service" here is a tenant-configurable catalog item (Corte, Barba, CB, ...)
with a `duration_minutes` and a `price_cents`. The platform code `C/B/CB` is
not on the service row — it's a convention enforced by the application
layer when matching a service to a `ServiceCode`.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.infrastructure.db.models.scheduling import Service
from packages.infrastructure.repositories.base import TenantScopedRepository


class ServiceRepository(TenantScopedRepository[Service]):
    model = Service

    def list_active(self) -> list[Service]:
        stmt = self._by_tenant_stmt().where(Service.is_active.is_(True))
        return list(self._session.execute(stmt).scalars())

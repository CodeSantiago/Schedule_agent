"""Tenant-scoped repository for `tenants` and `tenant_settings`.

Note: this is the *one* repo that does not have a `tenant_id` column on
its main table — tenants ARE the tenancy root. We treat the row's own
`id` as the tenant scope (so a TenantRepository bound to id X will only
operate on tenant X). The base class lets us reuse the same shape with a
custom `_tenant_column` so the API layer can inject one tenant at a time.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from packages.infrastructure.db.models.tenants import Tenant, TenantSetting
from packages.infrastructure.repositories.base import TenantScopedRepository


class TenantRepository(TenantScopedRepository[Tenant]):
    """Repo bound to a single tenant. The 'scope' is the tenant's own id."""

    model = Tenant
    _tenant_column = "id"  # tenants are scoped by their own id

    def get_settings(self) -> TenantSetting | None:
        return self._session.get(TenantSetting, self._tenant_id)

    def upsert_settings(self, config: dict) -> TenantSetting:
        """Create or replace the tenant's settings blob."""
        existing = self.get_settings()
        if existing is None:
            existing = TenantSetting(tenant_id=self._tenant_id, config=config)
            self._session.add(existing)
        else:
            existing.config = config
        return existing

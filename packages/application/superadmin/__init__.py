"""Superadmin tenant-management service.

`TenantRepository` is bound to one tenant at a time and is the right
tool for the tenant-scoped routes. The superadmin endpoints need to
list / create / suspend tenants across the whole platform, so this
service sits one level up: it owns the global Tenant view and
delegates per-tenant operations to `TenantRepository` when needed.
"""

from __future__ import annotations

import uuid
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from packages.infrastructure.db.models.tenants import Tenant, TenantSetting


class SuperadminTenantService:
    """Cross-tenant operations for the superadmin surface.

    Stateless. The session is committed by the caller (the route).
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    # --- Reads -------------------------------------------------------------

    def list_tenants(
        self, *, status: Optional[str] = None, limit: int = 200, offset: int = 0
    ) -> list[Tenant]:
        """Return tenants across the platform, newest first.

        `status` filters by tenant lifecycle state (e.g. `"trial"`).
        `limit` is capped at 500 to keep the list bounded.
        """
        bounded_limit = max(1, min(int(limit), 500))
        stmt = select(Tenant).order_by(Tenant.created_at.desc())
        if status is not None:
            stmt = stmt.where(Tenant.status == status)
        stmt = stmt.offset(max(0, int(offset))).limit(bounded_limit)
        return list(self._session.execute(stmt).scalars())

    def get(self, tenant_id: UUID) -> Tenant | None:
        return self._session.get(Tenant, tenant_id)

    # --- Writes ------------------------------------------------------------

    def create_tenant(
        self,
        *,
        name: str,
        slug: str,
        timezone: str = "UTC",
        status: str = "trial",
        initial_settings: Optional[dict] = None,
    ) -> Tenant:
        """Create a tenant row + an empty `tenant_settings` row in the
        same transaction. Raises `ValueError` on slug collision."""
        existing = self._session.execute(
            select(Tenant).where(Tenant.slug == slug)
        ).scalar_one_or_none()
        if existing is not None:
            raise ValueError(f"tenant with slug {slug!r} already exists")
        tenant = Tenant(
            id=uuid.uuid4(),
            name=name,
            slug=slug,
            status=status,
            timezone=timezone,
        )
        self._session.add(tenant)
        try:
            self._session.flush()
        except IntegrityError as exc:
            self._session.rollback()
            raise ValueError(f"tenant with slug {slug!r} already exists") from exc
        if initial_settings is not None:
            self._session.add(
                TenantSetting(tenant_id=tenant.id, config=initial_settings)
            )
            self._session.flush()
        return tenant

    def update_status(self, tenant_id: UUID, new_status: str) -> Tenant | None:
        """Flip a tenant's lifecycle state. Returns the updated row, or
        None if the tenant does not exist."""
        tenant = self._session.get(Tenant, tenant_id)
        if tenant is None:
            return None
        tenant.status = new_status
        self._session.flush()
        return tenant

    def soft_delete(self, tenant_id: UUID) -> Tenant | None:
        """Mark a tenant as `churned` — the soft-delete primitive.

        No rows are removed; the tenant row and its data (barbers,
        appointments, settings, provider configs) are preserved so an
        operator can reactivate it by flipping the status back to
        `active` or `trial`. Returns the updated row, or `None` when
        the tenant does not exist.
        """
        return self.update_status(tenant_id, "churned")

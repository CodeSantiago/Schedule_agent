"""Provider-config repository.

`ProviderConfig` is the per-tenant wiring row for one external service
(WhatsApp, LLM, calendar, ...). Most reads need "the active config for
this kind", so the repo exposes `get_active_for_kind` and
`list_for_kind`. Writes go through the standard `add` / `delete` from
the base; `add` is overridden to enforce the
`uq_provider_active_per_kind` constraint by deactivating any sibling
row when a new active row is created.
"""

from __future__ import annotations

import uuid
from typing import Any
from uuid import UUID

from sqlalchemy import select

from packages.infrastructure.db.models.providers import ProviderConfig
from packages.infrastructure.repositories.base import TenantScopedRepository


class ProviderConfigRepository(TenantScopedRepository[ProviderConfig]):
    model = ProviderConfig

    def get_by_id(self, row_id: UUID) -> ProviderConfig | None:  # type: ignore[override]
        return super().get_by_id(row_id)

    def list_for_kind(self, kind: str) -> list[ProviderConfig]:
        stmt = (
            self._by_tenant_stmt()
            .where(ProviderConfig.kind == kind)
            .order_by(ProviderConfig.is_active.desc(), ProviderConfig.created_at.asc())
        )
        return list(self._session.execute(stmt).scalars())

    def get_active_for_kind(self, kind: str) -> ProviderConfig | None:
        stmt = (
            self._by_tenant_stmt()
            .where(ProviderConfig.kind == kind)
            .where(ProviderConfig.is_active.is_(True))
        )
        return self._session.execute(stmt).scalar_one_or_none()

    def add(self, row: ProviderConfig) -> ProviderConfig:  # type: ignore[override]
        # Enforce the "at most one ACTIVE config per kind per tenant"
        # invariant at the application layer. The DB has a partial
        # unique constraint that catches concurrent inserts; here we
        # clear the bit on any sibling row AND flush before adding the
        # new row, so the unique index never sees two active rows in
        # the same statement batch.
        if row.is_active:
            existing_active = self.get_active_for_kind(row.kind)
            if existing_active is not None and existing_active.id != row.id:
                existing_active.is_active = False
                self._session.flush()
        # Force the tenant_id (the base does this too, but we want it
        # for rows constructed without one).
        if getattr(row, "tenant_id", None) is None:
            row.tenant_id = self._tenant_id  # type: ignore[attr-defined]
        result = super().add(row)
        self._session.flush()
        return result

    def set_active(self, row_id: UUID) -> ProviderConfig | None:
        """Flip `row_id` to `is_active=True` and deactivate every other
        row of the same kind in this tenant. Returns the activated row,
        or None if the row did not exist / was not ours.

        The deactivation must be flushed BEFORE the activation so the
        unique constraint `uq_provider_active_per_kind` is satisfied
        in a single statement batch.
        """
        target = self.get_by_id(row_id)
        if target is None:
            return None
        siblings = self.list_for_kind(target.kind)
        # Step 1: deactivate every OTHER sibling, then flush so the
        # partial unique index no longer has a conflicting row.
        # We deactivate by ID, not by current `is_active` value, so
        # the case where a row is already inactive still gets
        # handled (and a previously-inactive target gets reactivated
        # cleanly on step 2).
        for sib in siblings:
            if sib.id != target.id:
                sib.is_active = False
        self._session.flush()
        # Step 2: activate the target. If it was already active the
        # set is a no-op.
        target.is_active = True
        self._session.flush()
        return target

    def deactivate(self, row_id: UUID) -> bool:
        """Mark `row_id` as inactive. Returns True if the row was found
        and was previously active."""
        row = self.get_by_id(row_id)
        if row is None or not row.is_active:
            return False
        row.is_active = False
        self._session.flush()
        return True

"""Tenant-scoped repository for the audit / operational log table.

Provides a simple ``log()`` method to write entries and a ``list()``
with paging ordered by ``created_at DESC``.  The intent is that every
code path that needs to log calls ``repo.log(...)`` — a single line —
without worrying about the table shape or the default columns.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from packages.infrastructure.db.models.audit_log import TenantAuditLog
from packages.infrastructure.repositories.base import TenantScopedRepository


class TenantAuditLogRepository(TenantScopedRepository[TenantAuditLog]):
    """Repository for tenant-scoped audit / operational log entries."""

    model = TenantAuditLog

    def log(
        self,
        *,
        event_type: str,
        level: str = "info",
        message: str = "",
        actor_scope: str | None = None,
        actor_id: str | None = None,
        changed_fields: dict | None = None,
        details: dict[str, Any] | None = None,
        duration_ms: int | None = None,
    ) -> TenantAuditLog:
        """Write one log entry for the bound tenant.

        Parameters are a direct match for the ``TenantAuditLog`` columns.
        ``duration_ms`` is nullable and only meaningful when the caller
        has measured wall-clock time (e.g. a webhook processing duration).
        """
        entry = TenantAuditLog(
            tenant_id=self._tenant_id,
            event_type=event_type,
            level=level,
            message=message,
            actor_scope=actor_scope,
            actor_id=actor_id,
            changed_fields=changed_fields,
            details=details or {},
            duration_ms=duration_ms,
        )
        self._session.add(entry)
        return entry

    def list_recent(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        event_type: str | None = None,
    ) -> list[TenantAuditLog]:
        """Return recent log entries for this tenant, newest first.

        Supports optional ``event_type`` filtering and standard paging.
        """
        stmt = (
            select(TenantAuditLog)
            .where(TenantAuditLog.tenant_id == self._tenant_id)
            .order_by(desc(TenantAuditLog.created_at))
        )
        if event_type:
            stmt = stmt.where(TenantAuditLog.event_type == event_type)
        stmt = stmt.offset(offset).limit(limit)
        return list(self._session.execute(stmt).scalars())

"""Tenant-scoped audit / operational log endpoints.

These routes let tenant users and superadmins view recent operational
log entries for a tenant.

- GET ``/tenants/{tenant_id}/logs`` — tenant self-service
- GET ``/superadmin/tenants/{tenant_id}/logs`` — superadmin view

Both return the same shape but use different auth dependencies.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from apps.api.src.deps import (
    get_current_principal,
    get_session,
    get_tenant_repo,
    require_tenant,
    tenant_id_from_path,
)
from apps.api.src.schemas import TenantAuditLogListOut, TenantAuditLogOut
from packages.infrastructure.repositories import TenantAuditLogRepository

tenant_router = APIRouter(tags=["audit-logs"])
superadmin_router = APIRouter(tags=["audit-logs"])


def _log_repo(session: Session, tenant_id: UUID) -> TenantAuditLogRepository:
    return TenantAuditLogRepository(session, tenant_id)


@tenant_router.get(
    "/tenants/{tenant_id}/logs",
    response_model=TenantAuditLogListOut,
)
def list_tenant_logs(
    tenant_id: UUID = Depends(require_tenant),
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    event_type: Annotated[str | None, Query(max_length=64)] = None,
    session: Session = Depends(get_session),
) -> TenantAuditLogListOut:
    """Return recent operational logs for the current tenant (tenant auth).

    Tenant users see only their own tenant's logs. Supports paging and
    optional ``event_type`` filtering.
    """
    repo = _log_repo(session, tenant_id)
    entries = repo.list_recent(limit=limit, offset=offset, event_type=event_type)
    return TenantAuditLogListOut(
        entries=[TenantAuditLogOut.model_validate(e) for e in entries],
        total=len(entries),
    )


@superadmin_router.get(
    "/superadmin/tenants/{tenant_id}/logs",
    response_model=TenantAuditLogListOut,
)
def list_superadmin_logs(
    tenant_id: UUID = Depends(tenant_id_from_path),
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    event_type: Annotated[str | None, Query(max_length=64)] = None,
    session: Session = Depends(get_session),
    _principal=Depends(get_current_principal),
) -> TenantAuditLogListOut:
    """Return recent operational logs for any tenant (superadmin view).

    Superadmins can inspect logs of any tenant. Supports paging and
    optional ``event_type`` filtering.
    """
    repo = _log_repo(session, tenant_id)
    entries = repo.list_recent(limit=limit, offset=offset, event_type=event_type)
    return TenantAuditLogListOut(
        entries=[TenantAuditLogOut.model_validate(e) for e in entries],
        total=len(entries),
    )

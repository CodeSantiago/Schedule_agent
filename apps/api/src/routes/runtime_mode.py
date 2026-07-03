"""Tenant runtime mode endpoint.

Returns the effective operating mode and sheets connection status for a
tenant. This is a read-only introspection endpoint that the admin settings
UI and the bot runtime can use to determine data-source routing.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from apps.api.src.deps import (
    get_session,
    get_tenant_principal,
    require_tenant,
)
from packages.application.tenant.mode_service import TenantModeService
from sqlalchemy.orm import Session

router = APIRouter(
    prefix="/tenants/{tenant_id}/runtime",
    tags=["runtime"],
)


class DomainSources(BaseModel):
    bot: str
    barber_status: str
    scheduling: str
    appointments: str
    general: str


class RuntimeConstraints(BaseModel):
    sheets_write_back: bool
    note: str


class RuntimeModeOut(BaseModel):
    mode: str
    sheets_connected: bool
    sheets_state: dict | None = None
    domains: DomainSources
    constraints: RuntimeConstraints


@router.get("/mode", response_model=RuntimeModeOut)
def get_runtime_mode(
    tenant_id: UUID = Depends(require_tenant),
    session: Session = Depends(get_session),
    _principal=Depends(get_tenant_principal),
) -> RuntimeModeOut:
    """Return the tenant's effective operating mode.

    Shows the configured ``source_of_truth``, whether sheets are
    connected, and which domains are served by which source.
    """
    svc = TenantModeService(session, tenant_id)
    summary = svc.get_runtime_summary()

    domains_data = summary.get("domains", {})
    constraints_data = summary.get("constraints", {})

    return RuntimeModeOut(
        mode=summary.get("mode", "database"),
        sheets_connected=summary.get("sheets_connected", False),
        sheets_state=summary.get("sheets_state"),
        domains=DomainSources(
            bot=domains_data.get("bot", "database"),
            barber_status=domains_data.get("barber_status", "database"),
            scheduling=domains_data.get("scheduling", "database"),
            appointments=domains_data.get("appointments", "database"),
            general=domains_data.get("general", "database"),
        ),
        constraints=RuntimeConstraints(
            sheets_write_back=constraints_data.get("sheets_write_back", False),
            note=constraints_data.get(
                "note",
                "Sheets mode is read-only. Use the admin dashboard for write operations.",
            ),
        ),
    )

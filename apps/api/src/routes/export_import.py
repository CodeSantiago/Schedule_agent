"""Per-tenant export/import (backup/restore) endpoints.

Provides a pragmatic first-pass backup/restore capability:

- ``GET /tenants/{tenant_id}/export`` — JSON snapshot of all tenant data.
- ``POST /tenants/{tenant_id}/import`` — Destructive restore from a JSON
  snapshot (replaces all data for the tenant).

The export includes: settings, barbers, services, schedules, absences,
extra hours, appointments (non-deleted), provider configs, and audit logs.

The import is **destructive** — it deletes ALL existing data for the
tenant before inserting the snapshot. This is a conscious tradeoff for
operational simplicity. Use with caution.
"""

from __future__ import annotations

import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from apps.api.src.deps import (
    get_current_principal,
    get_session,
    get_tenant_principal,
    require_tenant,
    tenant_id_from_path,
)
from packages.infrastructure.db.models.appointments import Appointment
from packages.infrastructure.db.models.audit_log import TenantAuditLog
from packages.infrastructure.db.models.providers import ProviderConfig
from packages.infrastructure.db.models.scheduling import (
    Barber,
    BarberAbsence,
    BarberExtraHour,
    BarberSchedule,
    Service,
)
from packages.infrastructure.db.models.tenants import Tenant, TenantSetting
from packages.infrastructure.repositories import TenantAuditLogRepository, TenantRepository

router = APIRouter(tags=["export-import"])


# ── Export ────────────────────────────────────────────────────────────────


class TenantExportData(BaseModel):
    """Full JSON snapshot of a tenant's data."""

    settings: dict | None = None
    barbers: list[dict]
    services: list[dict]
    schedules: list[dict]
    absences: list[dict]
    extra_hours: list[dict]
    appointments: list[dict]
    provider_configs: list[dict]
    audit_logs: list[dict]
    export_version: str = "1.0"
    exported_at: str = ""


def _row_as_dict(row) -> dict:
    """Convert an ORM row to a dict, excluding SQLAlchemy internal state."""
    d = {}
    for col in row.__table__.columns:
        val = getattr(row, col.name)
        # Convert UUID, datetime, date, time to strings.
        if isinstance(val, UUID):
            d[col.name] = str(val)
        elif hasattr(val, "isoformat"):
            d[col.name] = val.isoformat()
        else:
            d[col.name] = val
    return d


@router.get(
    "/tenants/{tenant_id}/export",
    response_model=TenantExportData,
)
def export_tenant(
    tenant_id: UUID = Depends(require_tenant),
    session: Session = Depends(get_session),
    principal=Depends(get_tenant_principal),
) -> TenantExportData:
    """Export a full JSON snapshot of the tenant's data.

    Requires ``manage_tenant_export_import`` permission (owner role).
    The snapshot can be used to restore the tenant via POST /import.
    """
    from datetime import datetime, timezone

    # Check permission.
    from packages.application.auth.rbac import check_permission

    if not check_permission(principal, "manage_tenant_export_import"):
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="insufficient permissions")

    now = datetime.now(timezone.utc).isoformat()

    def _dump(model):
        rows = session.execute(
            select(model).where(model.tenant_id == tenant_id)
        ).scalars().all()
        return [_row_as_dict(r) for r in rows]

    repo = TenantRepository(session, tenant_id)
    settings = repo.get_settings()
    settings_dict = _row_as_dict(settings) if settings else None

    return TenantExportData(
        settings=settings_dict,
        barbers=_dump(Barber),
        services=_dump(Service),
        schedules=_dump(BarberSchedule),
        absences=_dump(BarberAbsence),
        extra_hours=_dump(BarberExtraHour),
        appointments=_dump(Appointment),
        provider_configs=_dump(ProviderConfig),
        audit_logs=_dump(TenantAuditLog),
        export_version="1.0",
        exported_at=now,
    )


# ── Import ────────────────────────────────────────────────────────────────


class TenantImportResult(BaseModel):
    ok: bool
    message: str
    items_imported: dict[str, int]


@router.post(
    "/tenants/{tenant_id}/import",
    response_model=TenantImportResult,
)
def import_tenant(
    payload: TenantExportData,
    tenant_id: UUID = Depends(require_tenant),
    session: Session = Depends(get_session),
    principal=Depends(get_tenant_principal),
) -> TenantImportResult:
    """Destructive restore: deletes ALL data for the tenant and
    re-inserts from the provided snapshot.

    Requires ``manage_tenant_export_import`` permission (owner role).
    Use with extreme caution — this cannot be undone.
    """
    from packages.application.auth.rbac import check_permission

    if not check_permission(principal, "manage_tenant_export_import"):
        raise HTTPException(status_code=403, detail="insufficient permissions")

    counts: dict[str, int] = {}

    # Delete all existing data (order matters due to FK constraints).
    tables_to_clear = [
        TenantAuditLog,
        Appointment,
        BarberAbsence,
        BarberExtraHour,
        BarberSchedule,
        ProviderConfig,
        Barber,
        Service,
    ]
    for model in tables_to_clear:
        session.execute(
            delete(model).where(model.tenant_id == tenant_id)
        )
    # Also clear the settings row.
    session.execute(
        delete(TenantSetting).where(TenantSetting.tenant_id == tenant_id)
    )
    session.flush()

    # Import: settings.
    if payload.settings:
        settings_row = TenantSetting(
            tenant_id=tenant_id,
            config=payload.settings.get("config", {}),
        )
        session.add(settings_row)
        counts["settings"] = 1
    session.flush()

    # Helper to batch-import a list of dicts.
    def _import_model(model, rows: list[dict]) -> int:
        count = 0
        for row_data in rows:
            # Convert string UUIDs and dates back.
            cleaned = {}
            for key, val in row_data.items():
                if key in ("id", "tenant_id", "barber_id", "service_id",
                           "provider_config_id", "superadmin_id",
                           "tenant_user_id"):
                    cleaned[key] = UUID(val) if val else None
                elif key in ("created_at", "updated_at", "appointment_date",
                             "absence_date", "extra_date", "start_at",
                             "end_at"):
                    # Skip timestamps — let the DB default.
                    continue
                elif key in ("is_active",):
                    if isinstance(val, bool):
                        cleaned[key] = val
                    else:
                        cleaned[key] = val
                else:
                    cleaned[key] = val
            # Force tenant_id.
            if hasattr(model, "tenant_id"):
                cleaned["tenant_id"] = tenant_id
            row = model(**cleaned)
            session.add(row)
            count += 1
        return count

    counts["barbers"] = _import_model(Barber, payload.barbers or [])
    counts["services"] = _import_model(Service, payload.services or [])
    counts["schedules"] = _import_model(BarberSchedule, payload.schedules or [])
    counts["absences"] = _import_model(BarberAbsence, payload.absences or [])
    counts["extra_hours"] = _import_model(BarberExtraHour, payload.extra_hours or [])
    counts["appointments"] = _import_model(Appointment, payload.appointments or [])
    counts["provider_configs"] = _import_model(ProviderConfig, payload.provider_configs or [])
    counts["audit_logs"] = _import_model(TenantAuditLog, payload.audit_logs or [])

    session.commit()

    # Log the import event.
    audit = TenantAuditLogRepository(session, tenant_id)
    audit.log(
        event_type="data_imported",
        level="info",
        message=f"Tenant data restored from snapshot ({sum(counts.values())} items)",
        actor_scope="tenant",
        actor_id=str(principal.user_id) if principal else None,
        changed_fields=counts,
    )
    session.commit()

    return TenantImportResult(
        ok=True,
        message=f"Import complete: {sum(counts.values())} items restored",
        items_imported=counts,
    )

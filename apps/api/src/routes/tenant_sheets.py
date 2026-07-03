"""Tenant self-service Sheets provider config endpoints.

Tenant users manage ONLY their own ``kind=sheets`` provider config
here — no other provider kind, no other tenant's config. Every route
requires a tenant-scoped bearer token and verifies the path
``tenant_id`` matches the principal's tenant.

The existing admin ``/tenants/{tenant_id}/provider-configs/*`` routes
continue to work as-is for superadmins and can still manage any kind.

Audit log entries are written on create, update, activate, and delete.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from apps.api.src.deps import (
    get_provider_config_service,
    get_session,
    get_tenant_principal,
    require_tenant,
)
from apps.api.src.schemas import (
    TenantSheetsConfigCreate,
    TenantSheetsConfigOut,
    TenantSheetsConfigUpdate,
)
from packages.application.providers import ProviderConfigService
from packages.infrastructure.repositories import TenantAuditLogRepository

router = APIRouter(
    prefix="/tenants/{tenant_id}/sheets-config",
    tags=["tenant-sheets"],
)

SHEETS_KIND = "sheets"
SHEETS_DEFAULT_PROVIDER = "google_sheets"


def _get_sheets_config(
    tenant_id: UUID, svc: ProviderConfigService
) -> dict | None:
    """Return the active OR first inactive sheets config for this tenant.

    Returns ``None`` when no sheets config row exists at all.
    """
    # Prefer the active row; fall back to any inactive row.
    active = svc.get_active_for_kind(SHEETS_KIND)
    if active is not None:
        return _row_to_dict(active)
    rows = svc.list_for_kind(SHEETS_KIND)
    if rows:
        return _row_to_dict(rows[0])
    return None


def _row_to_dict(row) -> dict:
    return {
        "id": str(row.id),
        "kind": row.kind,
        "label": row.label,
        "provider_name": row.provider_name,
        "credentials": dict(row.credentials or {}),
        "settings": dict(row.settings or {}),
        "is_active": row.is_active,
    }


def _resolve_principal_id(principal) -> str | None:
    return str(getattr(principal, "user_id", "") or "")


# ── CRUD endpoints ────────────────────────────────────────────────────────


@router.get("", response_model=TenantSheetsConfigOut)
def get_sheets_config(
    tenant_id: UUID = Depends(require_tenant),
    svc: ProviderConfigService = Depends(get_provider_config_service),
    _principal=Depends(get_tenant_principal),
) -> TenantSheetsConfigOut:
    """Return the current sheets config for this tenant, or 404."""
    config = _get_sheets_config(tenant_id, svc)
    if config is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No sheets provider config found for this tenant",
        )
    return TenantSheetsConfigOut(**config)


@router.put(
    "",
    response_model=TenantSheetsConfigOut,
    status_code=status.HTTP_200_OK,
)
def create_or_update_sheets_config(
    payload: TenantSheetsConfigCreate,
    tenant_id: UUID = Depends(require_tenant),
    svc: ProviderConfigService = Depends(get_provider_config_service),
    session: Session = Depends(get_session),
    principal=Depends(get_tenant_principal),
) -> TenantSheetsConfigOut:
    """Create or update the tenant's sheets provider config (upsert).

    If a sheets config row already exists (active or inactive), update
    it in-place. Otherwise create a new row. Only ``kind=sheets``.
    """
    existing_rows = svc.list_for_kind(SHEETS_KIND)
    actor_id = _resolve_principal_id(principal)

    if existing_rows:
        # Update the first existing row (prefer active).
        target = existing_rows[0]
        for row in existing_rows:
            if row.is_active:
                target = row
                break
        row = svc.update(
            target.id,
            label=payload.label,
            provider_name=payload.provider_name or SHEETS_DEFAULT_PROVIDER,
            credentials=payload.credentials or {},
            settings=payload.settings or {},
            is_active=payload.is_active,
        )
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update sheets config",
            )
        session.commit()
        session.refresh(row)

        audit = TenantAuditLogRepository(session, tenant_id)
        audit.log(
            event_type="sheets_config_updated",
            level="info",
            message="Sheets provider config updated by tenant",
            actor_scope="tenant",
            actor_id=actor_id,
            changed_fields={"label": payload.label, "is_active": payload.is_active},
        )
        session.commit()
        return TenantSheetsConfigOut(**_row_to_dict(row))

    # No existing row → create.
    try:
        row = svc.create(
            kind=SHEETS_KIND,
            label=payload.label,
            provider_name=payload.provider_name or SHEETS_DEFAULT_PROVIDER,
            credentials=payload.credentials or {},
            settings=payload.settings or {},
            is_active=payload.is_active,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    session.commit()
    session.refresh(row)

    audit = TenantAuditLogRepository(session, tenant_id)
    audit.log(
        event_type="sheets_config_created",
        level="info",
        message="Sheets provider config created by tenant",
        actor_scope="tenant",
        actor_id=actor_id,
        changed_fields={"label": payload.label, "is_active": payload.is_active},
    )
    session.commit()
    return TenantSheetsConfigOut(**_row_to_dict(row))


@router.patch("", response_model=TenantSheetsConfigOut)
def update_sheets_config(
    payload: TenantSheetsConfigUpdate,
    tenant_id: UUID = Depends(require_tenant),
    svc: ProviderConfigService = Depends(get_provider_config_service),
    session: Session = Depends(get_session),
    principal=Depends(get_tenant_principal),
) -> TenantSheetsConfigOut:
    """Partial update of the tenant's sheets provider config.

    Only provided fields are changed. Returns 404 if no sheets config
    exists yet (use PUT to create).
    """
    existing_rows = svc.list_for_kind(SHEETS_KIND)
    if not existing_rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No sheets provider config found for this tenant; use PUT to create one",
        )
    actor_id = _resolve_principal_id(principal)

    # Prefer the active row.
    target = existing_rows[0]
    for row in existing_rows:
        if row.is_active:
            target = row
            break

    row = svc.update(
        target.id,
        label=payload.label,
        provider_name=payload.provider_name,
        credentials=payload.credentials,
        settings=payload.settings,
        is_active=payload.is_active,
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Sheets config not found",
        )
    session.commit()
    session.refresh(row)

    audit = TenantAuditLogRepository(session, tenant_id)
    audit.log(
        event_type="sheets_config_updated",
        level="info",
        message="Sheets provider config updated by tenant",
        actor_scope="tenant",
        actor_id=actor_id,
        changed_fields={
            k: v for k, v in payload.model_dump(exclude_none=True).items()
        },
    )
    session.commit()
    return TenantSheetsConfigOut(**_row_to_dict(row))


@router.post("/activate", response_model=TenantSheetsConfigOut)
def activate_sheets_config(
    tenant_id: UUID = Depends(require_tenant),
    svc: ProviderConfigService = Depends(get_provider_config_service),
    session: Session = Depends(get_session),
    principal=Depends(get_tenant_principal),
) -> TenantSheetsConfigOut:
    """Activate the tenant's sheets config (deactivates any sibling rows)."""
    rows = svc.list_for_kind(SHEETS_KIND)
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No sheets provider config to activate",
        )

    # If already active, just return it.
    for r in rows:
        if r.is_active:
            return TenantSheetsConfigOut(**_row_to_dict(r))

    activate_target = rows[0]
    row = svc.activate(activate_target.id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Sheets config not found for activation",
        )
    session.commit()
    session.refresh(row)

    actor_id = _resolve_principal_id(principal)
    audit = TenantAuditLogRepository(session, tenant_id)
    audit.log(
        event_type="sheets_config_activated",
        level="info",
        message="Sheets provider config activated by tenant",
        actor_scope="tenant",
        actor_id=actor_id,
    )
    session.commit()
    return TenantSheetsConfigOut(**_row_to_dict(row))


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def delete_sheets_config(
    tenant_id: UUID = Depends(require_tenant),
    svc: ProviderConfigService = Depends(get_provider_config_service),
    session: Session = Depends(get_session),
    principal=Depends(get_tenant_principal),
) -> None:
    """Delete the tenant's sheets provider config (active or inactive)."""
    rows = svc.list_for_kind(SHEETS_KIND)
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No sheets provider config to delete",
        )

    # Delete ALL sheets config rows for this tenant (clean sweep).
    for r in rows:
        svc.delete(r.id)
    session.commit()

    actor_id = _resolve_principal_id(principal)
    audit = TenantAuditLogRepository(session, tenant_id)
    audit.log(
        event_type="sheets_config_deleted",
        level="info",
        message="Sheets provider config deleted by tenant",
        actor_scope="tenant",
        actor_id=actor_id,
    )
    session.commit()

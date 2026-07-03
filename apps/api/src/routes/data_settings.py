"""Tenant data/integration policy endpoints.

These routes let tenant users and superadmins read/update the tenant's
data policy — source of truth (database, google_sheets, hybrid) and
sync mode (manual, import_only, import_export). The policy lives in
the ``data`` section of ``tenant_settings.config``.

Sheets connection status is derived from the active sheets provider
config (``provider_configs`` table).

Superadmin routes sit under ``/superadmin/tenants/{tenant_id}/settings/data``.
Tenant routes sit under ``/tenants/{tenant_id}/settings/data``.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from apps.api.src.deps import (
    get_current_principal,
    get_provider_config_service,
    get_session,
    get_tenant_principal,
    get_tenant_repo,
    require_tenant,
)
from apps.api.src.schemas import (
    TenantDataSettingsOut,
    TenantDataSettingsUpdate,
)
from packages.application.providers import ProviderConfigService
from packages.infrastructure.repositories import TenantAuditLogRepository, TenantRepository

tenant_router = APIRouter(
    prefix="/tenants/{tenant_id}/settings/data",
    tags=["data-settings"],
)
superadmin_router = APIRouter(
    prefix="/superadmin/tenants/{tenant_id}/settings/data",
    tags=["data-settings"],
)


# ── Helpers ────────────────────────────────────────────────────────────────

DATA_DEFAULTS = {"source_of_truth": "database", "sync_mode": "manual"}


def _read_data_config(repo: TenantRepository) -> dict:
    """Return the FULL config dict, ensuring data keys have defaults.

    Preserves every existing key so unrelated config sections
    (bot.*, business.*, booking.*, etc.) are NOT lost when the
    caller writes back via ``upsert_settings``.
    """
    settings = repo.get_settings()
    raw = dict(settings.config) if settings else {}
    data = raw.get("data")
    if not isinstance(data, dict):
        data = {}
        raw["data"] = data
    data.setdefault("source_of_truth", DATA_DEFAULTS["source_of_truth"])
    data.setdefault("sync_mode", DATA_DEFAULTS["sync_mode"])
    return raw


def _sheets_connected(tenant_id: UUID, svc: ProviderConfigService) -> bool:
    """Check whether the tenant has an active sheets provider config."""
    try:
        active = svc.get_active_for_kind("sheets")
        return active is not None
    except Exception:
        return False


def _build_response(
    config: dict, sheets_connected: bool
) -> TenantDataSettingsOut:
    data = config.get("data", {})
    return TenantDataSettingsOut(
        source_of_truth=data.get("source_of_truth", DATA_DEFAULTS["source_of_truth"]),
        sync_mode=data.get("sync_mode", DATA_DEFAULTS["sync_mode"]),
        sheets_connected=sheets_connected,
    )


# ── Tenant self-service ────────────────────────────────────────────────────


@tenant_router.get("", response_model=TenantDataSettingsOut)
def get_data_settings(
    repo: TenantRepository = Depends(get_tenant_repo),
    provider_svc: ProviderConfigService = Depends(get_provider_config_service),
    tenant_id: UUID = Depends(require_tenant),
) -> TenantDataSettingsOut:
    """Return the tenant's data/integration policy (tenant view).

    Always returns a well-formed response — missing settings rows
    are filled with defaults. ``sheets_connected`` reports whether
    an active sheets provider config exists.
    """
    config = _read_data_config(repo)
    connected = _sheets_connected(tenant_id, provider_svc)
    return _build_response(config, connected)


@tenant_router.put("", response_model=TenantDataSettingsOut)
def update_data_settings(
    payload: TenantDataSettingsUpdate,
    repo: TenantRepository = Depends(get_tenant_repo),
    provider_svc: ProviderConfigService = Depends(get_provider_config_service),
    session: Session = Depends(get_session),
    tenant_id: UUID = Depends(require_tenant),
    principal=Depends(get_tenant_principal),
) -> TenantDataSettingsOut:
    """Update the tenant's data/integration policy (tenant self-service).

    Only the provided fields are changed; omitted fields keep their
    current value. The change is recorded in the audit log.
    """
    config = _read_data_config(repo)
    changed: dict = {}

    if payload.source_of_truth is not None:
        config.setdefault("data", {})["source_of_truth"] = payload.source_of_truth
        changed["source_of_truth"] = payload.source_of_truth
    if payload.sync_mode is not None:
        config.setdefault("data", {})["sync_mode"] = payload.sync_mode
        changed["sync_mode"] = payload.sync_mode

    repo.upsert_settings(config)
    session.commit()

    # Audit log.
    audit = TenantAuditLogRepository(session, tenant_id)
    audit.log(
        event_type="data_settings_updated",
        level="info",
        message=f"Data/integration settings updated by tenant",
        actor_scope="tenant",
        actor_id=str(principal.user_id) if principal else None,
        changed_fields=changed,
    )
    session.commit()

    connected = _sheets_connected(tenant_id, provider_svc)
    return _build_response(config, connected)


# ── Superadmin ─────────────────────────────────────────────────────────────


@superadmin_router.get("", response_model=TenantDataSettingsOut)
def get_superadmin_data_settings(
    tenant_id: UUID,
    session: Session = Depends(get_session),
    provider_svc: ProviderConfigService = Depends(get_provider_config_service),
    _principal=Depends(get_current_principal),
) -> TenantDataSettingsOut:
    """Return a tenant's data/integration policy (superadmin view).

    Missing settings rows are filled with defaults.
    """
    repo = TenantRepository(session, tenant_id)
    config = _read_data_config(repo)
    connected = _sheets_connected(tenant_id, provider_svc)
    return _build_response(config, connected)


@superadmin_router.put("", response_model=TenantDataSettingsOut)
def update_superadmin_data_settings(
    tenant_id: UUID,
    payload: TenantDataSettingsUpdate,
    session: Session = Depends(get_session),
    provider_svc: ProviderConfigService = Depends(get_provider_config_service),
    principal=Depends(get_current_principal),
) -> TenantDataSettingsOut:
    """Update a tenant's data/integration policy (superadmin).

    Only the provided fields are changed; omitted fields keep their
    current value. The change is recorded in the audit log.
    """
    repo = TenantRepository(session, tenant_id)
    config = _read_data_config(repo)
    changed: dict = {}

    if payload.source_of_truth is not None:
        config.setdefault("data", {})["source_of_truth"] = payload.source_of_truth
        changed["source_of_truth"] = payload.source_of_truth
    if payload.sync_mode is not None:
        config.setdefault("data", {})["sync_mode"] = payload.sync_mode
        changed["sync_mode"] = payload.sync_mode

    repo.upsert_settings(config)
    session.commit()

    # Audit log.
    audit = TenantAuditLogRepository(session, tenant_id)
    audit.log(
        event_type="data_settings_updated",
        level="info",
        message=f"Data/integration settings updated by superadmin",
        actor_scope="superadmin",
        actor_id=str(principal.user_id) if hasattr(principal, "user_id") else None,
        changed_fields=changed,
    )
    session.commit()

    connected = _sheets_connected(tenant_id, provider_svc)
    return _build_response(config, connected)

"""Per-tenant feature flag endpoints.

Feature flags live in the ``features`` section of ``tenant_settings.config``
as a flat dictionary of boolean values:

.. code-block:: json

    {
      "features": {
        "cb_booking": true,
        "whatsapp_bot": true,
        "online_payments": false,
        "reports": false
      }
    }

Tenants can toggle their own flags if they have the ``manage_feature_flags``
permission (owner only). Superadmins can toggle any tenant's flags.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from apps.api.src.deps import (
    get_current_principal,
    get_session,
    get_tenant_principal,
    get_tenant_repo,
    require_tenant,
    tenant_id_from_path,
)
from packages.application.auth.rbac import check_permission, PermissionDeniedError
from packages.infrastructure.repositories import TenantAuditLogRepository, TenantRepository

tenant_router = APIRouter(
    prefix="/tenants/{tenant_id}/settings/features",
    tags=["feature-flags"],
)
superadmin_router = APIRouter(
    prefix="/superadmin/tenants/{tenant_id}/settings/features",
    tags=["feature-flags"],
)

# Known feature flags and their defaults.
KNOWN_FEATURES: dict[str, bool] = {
    "cb_booking": True,  # Corte+Barba 2-slot booking
    "whatsapp_bot": True,  # WhatsApp bot processing
    "online_payments": False,  # Online payment integration
    "reports": False,  # Reporting/analytics
    "advanced_scheduling": True,  # Extra hours, absences, overrides
}


class TenantFeatureFlagsOut(BaseModel):
    features: dict[str, bool]
    available_flags: list[str]


class TenantFeatureFlagsUpdate(BaseModel):
    features: dict[str, bool]


def _read_features(repo: TenantRepository) -> dict:
    """Return the features dict with defaults for known flags."""
    settings = repo.get_settings()
    raw = dict(settings.config) if settings else {}
    features = raw.get("features")
    if not isinstance(features, dict):
        features = {}
        raw["features"] = features
    for flag, default in KNOWN_FEATURES.items():
        features.setdefault(flag, default)
    return raw


def _build_response(features: dict) -> TenantFeatureFlagsOut:
    return TenantFeatureFlagsOut(
        features=features,
        available_flags=sorted(KNOWN_FEATURES.keys()),
    )


# ── Tenant self-service ──────────────────────────────────────────────────


@tenant_router.get("", response_model=TenantFeatureFlagsOut)
def get_features(
    repo: TenantRepository = Depends(get_tenant_repo),
    tenant_id: UUID = Depends(require_tenant),
) -> TenantFeatureFlagsOut:
    """Return the tenant's feature flags."""
    raw = _read_features(repo)
    features = raw.get("features", {})
    return _build_response(features)


@tenant_router.put("", response_model=TenantFeatureFlagsOut)
def update_features(
    payload: TenantFeatureFlagsUpdate,
    repo: TenantRepository = Depends(get_tenant_repo),
    session: Session = Depends(get_session),
    tenant_id: UUID = Depends(require_tenant),
    principal=Depends(get_tenant_principal),
) -> TenantFeatureFlagsOut:
    """Update the tenant's feature flags.

    Requires ``manage_feature_flags`` permission (owner role).
    Only known flags are accepted; unknown flags are silently ignored.
    """
    if not check_permission(principal, "manage_feature_flags"):
        raise PermissionDeniedError()

    raw = _read_features(repo)
    current = raw.setdefault("features", {})
    changed: dict = {}
    for flag, value in payload.features.items():
        if flag in KNOWN_FEATURES:
            current[flag] = value
            changed[flag] = value

    repo.upsert_settings(raw)
    session.commit()

    audit = TenantAuditLogRepository(session, tenant_id)
    audit.log(
        event_type="feature_flags_updated",
        level="info",
        message="Feature flags updated",
        actor_scope="tenant",
        actor_id=str(principal.user_id) if principal else None,
        changed_fields=changed,
    )
    session.commit()

    return _build_response(current)


# ── Superadmin ───────────────────────────────────────────────────────────


@superadmin_router.get("", response_model=TenantFeatureFlagsOut)
def get_superadmin_features(
    tenant_id: UUID = Depends(tenant_id_from_path),
    session: Session = Depends(get_session),
    _principal=Depends(get_current_principal),
) -> TenantFeatureFlagsOut:
    """Return a tenant's feature flags (superadmin view)."""
    repo = TenantRepository(session, tenant_id)
    raw = _read_features(repo)
    features = raw.get("features", {})
    return _build_response(features)


@superadmin_router.put("", response_model=TenantFeatureFlagsOut)
def update_superadmin_features(
    tenant_id: UUID = Depends(tenant_id_from_path),
    payload: TenantFeatureFlagsUpdate = ...,
    session: Session = Depends(get_session),
    principal=Depends(get_current_principal),
) -> TenantFeatureFlagsOut:
    """Update a tenant's feature flags (superadmin)."""
    repo = TenantRepository(session, tenant_id)
    raw = _read_features(repo)
    current = raw.setdefault("features", {})
    for flag, value in payload.features.items():
        if flag in KNOWN_FEATURES:
            current[flag] = value

    repo.upsert_settings(raw)
    session.commit()

    audit = TenantAuditLogRepository(session, tenant_id)
    audit.log(
        event_type="feature_flags_updated",
        level="info",
        message="Feature flags updated by superadmin",
        actor_scope="superadmin",
        actor_id=str(principal.user_id) if hasattr(principal, "user_id") else None,
    )
    session.commit()

    return _build_response(current)

"""Customer identity settings endpoint.

Lets tenants configure how customer identity is captured during booking:

- ``full_name`` — single name field (default, backward compatible)
- ``first_name_last_name`` — first + last name concatenated
- ``dni`` — only DNI/national ID
- ``full_name_dni`` — full name + DNI
- ``first_name_last_name_dni`` — first + last name + DNI

The setting lives in ``tenant_settings.config.booking.customer_identity_mode``.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from apps.api.src.deps import (
    get_session,
    get_tenant_principal,
    get_tenant_repo,
    require_tenant,
)
from apps.api.src.schemas import (
    CUSTOMER_IDENTITY_MODES,
    TenantIdentitySettingsOut,
    TenantIdentitySettingsUpdate,
)
from packages.infrastructure.repositories import TenantRepository

router = APIRouter(
    prefix="/tenants/{tenant_id}/settings/identity",
    tags=["identity"],
)


@router.get("", response_model=TenantIdentitySettingsOut)
def get_identity_settings(
    repo: TenantRepository = Depends(get_tenant_repo),
    tenant_id: UUID = Depends(require_tenant),
    _principal=Depends(get_tenant_principal),
) -> TenantIdentitySettingsOut:
    """Return the current customer identity capture mode."""
    mode = _read_identity_mode(repo)
    return TenantIdentitySettingsOut(mode=mode)


@router.put("", response_model=TenantIdentitySettingsOut)
def update_identity_settings(
    payload: TenantIdentitySettingsUpdate,
    repo: TenantRepository = Depends(get_tenant_repo),
    session: Session = Depends(get_session),
    tenant_id: UUID = Depends(require_tenant),
    _principal=Depends(get_tenant_principal),
) -> TenantIdentitySettingsOut:
    """Update customer identity capture mode."""
    if payload.mode not in CUSTOMER_IDENTITY_MODES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid mode '{payload.mode}'. Valid modes: {', '.join(sorted(CUSTOMER_IDENTITY_MODES))}",
        )

    settings = repo.get_settings()
    config = dict(settings.config) if settings else {}
    booking = config.setdefault("booking", {})
    booking["customer_identity_mode"] = payload.mode
    repo.upsert_settings(config)
    session.commit()

    return TenantIdentitySettingsOut(mode=payload.mode)


# ── Helper ────────────────────────────────────────────────────────────────


def _read_identity_mode(repo: TenantRepository) -> str:
    settings = repo.get_settings()
    if settings is None:
        return "full_name"
    config = dict(settings.config or {})
    booking = config.get("booking", {})
    if not isinstance(booking, dict):
        return "full_name"
    mode = booking.get("customer_identity_mode", "full_name")
    if mode not in CUSTOMER_IDENTITY_MODES:
        return "full_name"
    return mode

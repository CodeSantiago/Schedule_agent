"""Provider-config endpoints (per-tenant, superadmin-protected).

These CRUD the `provider_configs` rows that wire a tenant to a
messaging / LLM / calendar provider. Every route requires a
superadmin bearer token; the per-tenant scope is the `tenant_id` path
parameter.

Writes go through `ProviderConfigService`. The service and repo enforce
the "at most one active config per kind per tenant" invariant on every
activation path (create, update, /activate), so the routes don't have
to. The partial unique index `uq_provider_active_per_kind` is the
last line of defence.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from apps.api.src.deps import (
    TenantPath,
    get_current_principal,
    get_provider_config_service,
)
from apps.api.src.schemas import (
    ProviderConfigCreate,
    ProviderConfigOut,
    ProviderConfigUpdate,
)
from packages.application.providers import (
    ProviderConfigService,
    UnknownProviderKindError,
)

router = APIRouter(
    prefix="/tenants/{tenant_id}/provider-configs",
    tags=["provider-configs"],
)


@router.get("", response_model=list[ProviderConfigOut])
def list_provider_configs(
    tenant_id: TenantPath,
    kind: Annotated[
        str | None,
        Query(description="Filter by provider kind (e.g. whatsapp, llm)"),
    ] = None,
    service: ProviderConfigService = Depends(get_provider_config_service),
    _principal=Depends(get_current_principal),
) -> list[ProviderConfigOut]:
    if kind is None:
        # No kind filter: aggregate across all kinds the tenant uses.
        # We still cap to 500 rows so a misbehaving caller cannot
        # accidentally pull the whole table.
        rows = []
        for k in ("whatsapp", "llm", "calendar", "sheets", "sms"):
            try:
                rows.extend(service.list_for_kind(k))
            except UnknownProviderKindError:
                # Unknown kinds are skipped (defensive — should not
                # happen with the current enum).
                continue
        return [ProviderConfigOut.model_validate(r) for r in rows[:500]]
    try:
        rows = service.list_for_kind(kind)
    except UnknownProviderKindError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return [ProviderConfigOut.model_validate(r) for r in rows]


@router.post(
    "",
    response_model=ProviderConfigOut,
    status_code=status.HTTP_201_CREATED,
)
def create_provider_config(
    tenant_id: TenantPath,
    payload: ProviderConfigCreate,
    service: ProviderConfigService = Depends(get_provider_config_service),
    _principal=Depends(get_current_principal),
) -> ProviderConfigOut:
    try:
        row = service.create(
            kind=payload.kind,
            label=payload.label,
            provider_name=payload.provider_name,
            credentials=payload.credentials,
            settings=payload.settings,
            is_active=payload.is_active,
        )
    except UnknownProviderKindError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    service.session.commit()
    service.session.refresh(row)
    return ProviderConfigOut.model_validate(row)


@router.get("/{config_id}", response_model=ProviderConfigOut)
def get_provider_config(
    tenant_id: TenantPath,
    config_id: UUID,
    service: ProviderConfigService = Depends(get_provider_config_service),
    _principal=Depends(get_current_principal),
) -> ProviderConfigOut:
    row = service.get(config_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"provider config {config_id} not found for this tenant",
        )
    return ProviderConfigOut.model_validate(row)


@router.patch("/{config_id}", response_model=ProviderConfigOut)
def update_provider_config(
    tenant_id: TenantPath,
    config_id: UUID,
    payload: ProviderConfigUpdate,
    service: ProviderConfigService = Depends(get_provider_config_service),
    _principal=Depends(get_current_principal),
) -> ProviderConfigOut:
    try:
        row = service.update(
            config_id,
            label=payload.label,
            provider_name=payload.provider_name,
            credentials=payload.credentials,
            settings=payload.settings,
            is_active=payload.is_active,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"provider config {config_id} not found for this tenant",
        )
    service.session.commit()
    return ProviderConfigOut.model_validate(row)


@router.post(
    "/{config_id}/activate",
    response_model=ProviderConfigOut,
)
def activate_provider_config(
    tenant_id: TenantPath,
    config_id: UUID,
    service: ProviderConfigService = Depends(get_provider_config_service),
    _principal=Depends(get_current_principal),
) -> ProviderConfigOut:
    row = service.activate(config_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"provider config {config_id} not found for this tenant",
        )
    service.session.commit()
    return ProviderConfigOut.model_validate(row)


@router.delete(
    "/{config_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_provider_config(
    tenant_id: TenantPath,
    config_id: UUID,
    service: ProviderConfigService = Depends(get_provider_config_service),
    _principal=Depends(get_current_principal),
) -> None:
    if not service.delete(config_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"provider config {config_id} not found for this tenant",
        )
    service.session.commit()

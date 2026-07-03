"""Service endpoints (per-tenant)."""

from __future__ import annotations

from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError

from apps.api.src.deps import get_service_repo, require_tenant
from apps.api.src.schemas import ServiceCreate, ServiceOut, ServiceUpdate
from packages.infrastructure.db.models.scheduling import Service
from packages.infrastructure.repositories import ServiceRepository

router = APIRouter(prefix="/tenants/{tenant_id}/services", tags=["services"])


@router.get("", response_model=list[ServiceOut])
def list_services(
    repo: ServiceRepository = Depends(get_service_repo),
    tenant_id: UUID = Depends(require_tenant),
) -> list[ServiceOut]:
    return [ServiceOut.model_validate(s) for s in repo.list()]


@router.post(
    "",
    response_model=ServiceOut,
    status_code=status.HTTP_201_CREATED,
)
def create_service(
    payload: ServiceCreate,
    repo: ServiceRepository = Depends(get_service_repo),
    tenant_id: UUID = Depends(require_tenant),
) -> ServiceOut:
    service = Service(
        id=uuid4(),
        tenant_id=tenant_id,
        name=payload.name,
        code=payload.code,
        duration_minutes=payload.duration_minutes,
        price_cents=payload.price_cents,
        description=payload.description,
        is_active=payload.is_active,
    )
    repo.add(service)
    repo.session.commit()
    repo.session.refresh(service)
    return ServiceOut.model_validate(service)


@router.get("/{service_id}", response_model=ServiceOut)
def get_service(
    service_id: UUID,
    repo: ServiceRepository = Depends(get_service_repo),
    tenant_id: UUID = Depends(require_tenant),
) -> ServiceOut:
    service = repo.get_by_id(service_id)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"service {service_id} not found for this tenant",
        )
    return ServiceOut.model_validate(service)


@router.put("/{service_id}", response_model=ServiceOut)
def update_service(
    service_id: UUID,
    payload: ServiceUpdate,
    repo: ServiceRepository = Depends(get_service_repo),
    tenant_id: UUID = Depends(require_tenant),
) -> ServiceOut:
    service = repo.get_by_id(service_id)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"service {service_id} not found for this tenant",
        )
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(service, field, value)
    repo.session.commit()
    repo.session.refresh(service)
    return ServiceOut.model_validate(service)


@router.delete("/{service_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_service(
    service_id: UUID,
    repo: ServiceRepository = Depends(get_service_repo),
    tenant_id: UUID = Depends(require_tenant),
) -> None:
    service = repo.get_by_id(service_id)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"service {service_id} not found for this tenant",
        )
    try:
        removed = repo.delete(service_id)
        if not removed:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"service {service_id} not found for this tenant",
            )
        repo.session.commit()
    except IntegrityError as exc:
        repo.session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="service is in use by existing appointments and cannot be deleted",
        ) from exc

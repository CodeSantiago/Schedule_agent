"""Barber endpoints (per-tenant)."""

from __future__ import annotations

from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status

from apps.api.src.deps import get_barber_repo, require_tenant
from apps.api.src.schemas import BarberCreate, BarberOut, BarberUpdate
from packages.infrastructure.db.models.scheduling import Barber
from packages.infrastructure.repositories import BarberRepository

router = APIRouter(prefix="/tenants/{tenant_id}/barbers", tags=["barbers"])


@router.get("", response_model=list[BarberOut])
def list_barbers(
    repo: BarberRepository = Depends(get_barber_repo),
    tenant_id: UUID = Depends(require_tenant),
) -> list[BarberOut]:
    return [BarberOut.model_validate(b) for b in repo.list()]


@router.post(
    "",
    response_model=BarberOut,
    status_code=status.HTTP_201_CREATED,
)
def create_barber(
    payload: BarberCreate,
    repo: BarberRepository = Depends(get_barber_repo),
    tenant_id: UUID = Depends(require_tenant),
) -> BarberOut:
    barber = Barber(
        id=uuid4(),
        tenant_id=tenant_id,
        name=payload.name,
        restrictions=payload.restrictions,
        is_active=payload.is_active,
    )
    repo.add(barber)
    repo.session.commit()
    repo.session.refresh(barber)
    return BarberOut.model_validate(barber)


@router.get("/{barber_id}", response_model=BarberOut)
def get_barber(
    barber_id: UUID,
    repo: BarberRepository = Depends(get_barber_repo),
    tenant_id: UUID = Depends(require_tenant),
) -> BarberOut:
    barber = repo.get_by_id(barber_id)
    if barber is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"barber {barber_id} not found for this tenant",
        )
    return BarberOut.model_validate(barber)


@router.put("/{barber_id}", response_model=BarberOut)
def update_barber(
    barber_id: UUID,
    payload: BarberUpdate,
    repo: BarberRepository = Depends(get_barber_repo),
    tenant_id: UUID = Depends(require_tenant),
) -> BarberOut:
    barber = repo.get_by_id(barber_id)
    if barber is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"barber {barber_id} not found for this tenant",
        )
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(barber, field, value)
    repo.session.commit()
    repo.session.refresh(barber)
    return BarberOut.model_validate(barber)

"""Extra-hours endpoints (per-tenant, per-barber)."""

from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status

from apps.api.src.deps import get_barber_repo, get_extra_hour_repo, require_tenant
from apps.api.src.schemas import ExtraHourCreate, ExtraHourOut, ExtraHourUpdate
from packages.infrastructure.db.models.scheduling import BarberExtraHour
from packages.infrastructure.repositories import BarberRepository, ExtraHourRepository

router = APIRouter(
    prefix="/tenants/{tenant_id}/barbers/{barber_id}/extra-hours",
    tags=["extra-hours"],
)


@router.get("", response_model=list[ExtraHourOut])
def list_extra_hours(
    barber_id: UUID,
    tenant_id: UUID = Depends(require_tenant),
    date_from: date | None = None,
    date_to: date | None = None,
    repo: ExtraHourRepository = Depends(get_extra_hour_repo),
    barber_repo: BarberRepository = Depends(get_barber_repo),
) -> list[ExtraHourOut]:
    if barber_repo.get_by_id(barber_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"barber {barber_id} not found for this tenant",
        )
    rows = repo.list_for_barber(barber_id, date_from=date_from, date_to=date_to)
    return [ExtraHourOut.model_validate(r) for r in rows]


@router.post(
    "",
    response_model=ExtraHourOut,
    status_code=status.HTTP_201_CREATED,
)
def create_extra_hour(
    payload: ExtraHourCreate,
    barber_id: UUID,
    repo: ExtraHourRepository = Depends(get_extra_hour_repo),
    barber_repo: BarberRepository = Depends(get_barber_repo),
    tenant_id: UUID = Depends(require_tenant),
) -> ExtraHourOut:
    if barber_repo.get_by_id(barber_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"barber {barber_id} not found for this tenant",
        )
    row = BarberExtraHour(
        id=uuid4(),
        barber_id=barber_id,
        extra_date=payload.extra_date,
        start_time=payload.start_time,
        end_time=payload.end_time,
        reason=payload.reason,
    )
    repo.add(row)
    repo.session.commit()
    repo.session.refresh(row)
    return ExtraHourOut.model_validate(row)


@router.put("/{extra_hour_id}", response_model=ExtraHourOut)
def update_extra_hour(
    extra_hour_id: UUID,
    payload: ExtraHourUpdate,
    barber_id: UUID,
    repo: ExtraHourRepository = Depends(get_extra_hour_repo),
    barber_repo: BarberRepository = Depends(get_barber_repo),
    tenant_id: UUID = Depends(require_tenant),
) -> ExtraHourOut:
    if barber_repo.get_by_id(barber_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"barber {barber_id} not found for this tenant",
        )
    row = repo.get_by_id(extra_hour_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"extra hour {extra_hour_id} not found for this tenant",
        )
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    repo.session.commit()
    repo.session.refresh(row)
    return ExtraHourOut.model_validate(row)


@router.delete(
    "/{extra_hour_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_extra_hour(
    extra_hour_id: UUID,
    barber_id: UUID,
    repo: ExtraHourRepository = Depends(get_extra_hour_repo),
    tenant_id: UUID = Depends(require_tenant),
) -> None:
    if not repo.delete(extra_hour_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"extra hour {extra_hour_id} not found for this tenant",
        )
    repo.session.commit()

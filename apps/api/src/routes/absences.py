"""Absence endpoints (per-tenant, per-barber)."""

from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status

from apps.api.src.deps import get_absence_repo, get_barber_repo, require_tenant
from apps.api.src.schemas import AbsenceCreate, AbsenceOut, AbsenceUpdate
from packages.infrastructure.db.models.scheduling import BarberAbsence
from packages.infrastructure.repositories import AbsenceRepository, BarberRepository

router = APIRouter(
    prefix="/tenants/{tenant_id}/barbers/{barber_id}/absences",
    tags=["absences"],
)


@router.get("", response_model=list[AbsenceOut])
def list_absences(
    barber_id: UUID,
    tenant_id: UUID = Depends(require_tenant),
    date_from: date | None = None,
    date_to: date | None = None,
    repo: AbsenceRepository = Depends(get_absence_repo),
    barber_repo: BarberRepository = Depends(get_barber_repo),
) -> list[AbsenceOut]:
    if barber_repo.get_by_id(barber_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"barber {barber_id} not found for this tenant",
        )
    rows = repo.list_for_barber(barber_id, date_from=date_from, date_to=date_to)
    return [AbsenceOut.model_validate(r) for r in rows]


@router.post(
    "",
    response_model=AbsenceOut,
    status_code=status.HTTP_201_CREATED,
)
def create_absence(
    payload: AbsenceCreate,
    barber_id: UUID,
    repo: AbsenceRepository = Depends(get_absence_repo),
    barber_repo: BarberRepository = Depends(get_barber_repo),
    tenant_id: UUID = Depends(require_tenant),
) -> AbsenceOut:
    if barber_repo.get_by_id(barber_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"barber {barber_id} not found for this tenant",
        )
    row = BarberAbsence(
        id=uuid4(),
        barber_id=barber_id,
        absence_date=payload.absence_date,
        start_time=payload.start_time,
        end_time=payload.end_time,
        reason=payload.reason,
    )
    repo.add(row)
    repo.session.commit()
    repo.session.refresh(row)
    return AbsenceOut.model_validate(row)


@router.put("/{absence_id}", response_model=AbsenceOut)
def update_absence(
    absence_id: UUID,
    payload: AbsenceUpdate,
    barber_id: UUID,
    repo: AbsenceRepository = Depends(get_absence_repo),
    barber_repo: BarberRepository = Depends(get_barber_repo),
    tenant_id: UUID = Depends(require_tenant),
) -> AbsenceOut:
    if barber_repo.get_by_id(barber_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"barber {barber_id} not found for this tenant",
        )
    row = repo.get_by_id(absence_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"absence {absence_id} not found for this tenant",
        )
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    repo.session.commit()
    repo.session.refresh(row)
    return AbsenceOut.model_validate(row)


@router.delete(
    "/{absence_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_absence(
    absence_id: UUID,
    barber_id: UUID,
    repo: AbsenceRepository = Depends(get_absence_repo),
    tenant_id: UUID = Depends(require_tenant),
) -> None:
    if not repo.delete(absence_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"absence {absence_id} not found for this tenant",
        )
    repo.session.commit()

"""Weekly schedule endpoints (per-tenant, per-barber)."""

from __future__ import annotations

from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status

from apps.api.src.deps import get_barber_repo, get_schedule_repo, require_tenant
from apps.api.src.schemas import ScheduleCreate, ScheduleOut, ScheduleUpdate
from packages.infrastructure.db.models.scheduling import BarberSchedule
from packages.infrastructure.repositories import BarberRepository, ScheduleRepository

router = APIRouter(
    prefix="/tenants/{tenant_id}/barbers/{barber_id}/schedules",
    tags=["schedules"],
)


@router.get("", response_model=list[ScheduleOut])
def list_schedules(
    barber_id: UUID,
    schedule_repo: ScheduleRepository = Depends(get_schedule_repo),
    barber_repo: BarberRepository = Depends(get_barber_repo),
    tenant_id: UUID = Depends(require_tenant),
) -> list[ScheduleOut]:
    if barber_repo.get_by_id(barber_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"barber {barber_id} not found for this tenant",
        )
    rows = schedule_repo.list_for_barber(barber_id)
    return [ScheduleOut.model_validate(r) for r in rows]


@router.post(
    "",
    response_model=ScheduleOut,
    status_code=status.HTTP_201_CREATED,
)
def create_schedule(
    payload: ScheduleCreate,
    barber_id: UUID,
    schedule_repo: ScheduleRepository = Depends(get_schedule_repo),
    barber_repo: BarberRepository = Depends(get_barber_repo),
    tenant_id: UUID = Depends(require_tenant),
) -> ScheduleOut:
    if barber_repo.get_by_id(barber_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"barber {barber_id} not found for this tenant",
        )
    row = BarberSchedule(
        id=uuid4(),
        barber_id=barber_id,
        weekday=payload.weekday.lower(),
        start_time=payload.start_time,
        end_time=payload.end_time,
    )
    schedule_repo.add(row)
    schedule_repo.session.commit()
    schedule_repo.session.refresh(row)
    return ScheduleOut.model_validate(row)


@router.put("/{schedule_id}", response_model=ScheduleOut)
def update_schedule(
    schedule_id: UUID,
    payload: ScheduleUpdate,
    barber_id: UUID,
    schedule_repo: ScheduleRepository = Depends(get_schedule_repo),
    barber_repo: BarberRepository = Depends(get_barber_repo),
    tenant_id: UUID = Depends(require_tenant),
) -> ScheduleOut:
    if barber_repo.get_by_id(barber_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"barber {barber_id} not found for this tenant",
        )
    row = schedule_repo.get_by_id(schedule_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"schedule {schedule_id} not found for this tenant",
        )
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    schedule_repo.session.commit()
    schedule_repo.session.refresh(row)
    return ScheduleOut.model_validate(row)


@router.delete(
    "/{schedule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_schedule(
    schedule_id: UUID,
    barber_id: UUID,
    schedule_repo: ScheduleRepository = Depends(get_schedule_repo),
    barber_repo: BarberRepository = Depends(get_barber_repo),
    tenant_id: UUID = Depends(require_tenant),
) -> None:
    if barber_repo.get_by_id(barber_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"barber {barber_id} not found for this tenant",
        )
    if not schedule_repo.delete(schedule_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"schedule {schedule_id} not found for this tenant",
        )
    schedule_repo.session.commit()

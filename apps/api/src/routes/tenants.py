"""Tenant management endpoints.

In Part 2 this is intentionally minimal: create + read. Auth and
multi-step onboarding (e.g. invitation flow) come later.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from apps.api.src.deps import get_session
from apps.api.src.schemas import TenantCreate, TenantOut
from packages.infrastructure.db.models.tenants import Tenant
from packages.infrastructure.repositories import TenantRepository

router = APIRouter(prefix="/tenants", tags=["tenants"])


@router.post(
    "",
    response_model=TenantOut,
    status_code=status.HTTP_201_CREATED,
)
def create_tenant(
    payload: TenantCreate,
    session: Session = Depends(get_session),
) -> TenantOut:
    """Create a new tenant. Slug must be unique; conflict -> 409."""
    from sqlalchemy import select

    existing = session.execute(
        select(Tenant).where(Tenant.slug == payload.slug)
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"tenant with slug {payload.slug!r} already exists",
        )
    tenant = Tenant(
        id=uuid4(),
        name=payload.name,
        slug=payload.slug,
        timezone=payload.timezone,
        status="trial",
    )
    session.add(tenant)
    session.commit()
    session.refresh(tenant)
    return TenantOut.model_validate(tenant)


@router.get("/{tenant_id}", response_model=TenantOut)
def get_tenant(
    tenant_id: UUID,
    session: Session = Depends(get_session),
) -> TenantOut:
    repo = TenantRepository(session, tenant_id)
    # `TenantRepository` is scoped by id (the tenant IS the scope), so
    # `get_by_id` returns the tenant itself.
    tenant = repo.get_by_id(tenant_id)
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"tenant {tenant_id} not found",
        )
    return TenantOut.model_validate(tenant)

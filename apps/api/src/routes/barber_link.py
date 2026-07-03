"""Barber-user link endpoint.

Lets tenant admins link a tenant user to a barber identity so that
when the barber logs in, they land directly on their own workspace
agenda without needing a barber selector.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.src.deps import (
    get_session,
    get_tenant_principal,
    require_tenant,
)
from packages.application.auth.rbac import check_permission, PermissionDeniedError
from packages.infrastructure.db.models.scheduling import Barber
from packages.infrastructure.db.models.tenant_user import TenantUser
from packages.infrastructure.repositories import TenantAuditLogRepository

router = APIRouter(
    prefix="/tenants/{tenant_id}/barber-link",
    tags=["barber-link"],
)


class BarberLinkOut(BaseModel):
    user_id: str
    email: str
    barber_id: str | None = None
    barber_name: str | None = None


class BarberLinkUpdate(BaseModel):
    barber_id: UUID | None = None  # null to unlink


@router.get("", response_model=list[BarberLinkOut])
def list_barber_links(
    tenant_id: UUID = Depends(require_tenant),
    session: Session = Depends(get_session),
    principal=Depends(get_tenant_principal),
) -> list[BarberLinkOut]:
    """List all tenant users with their linked barber (if any)."""
    if not check_permission(principal, "manage_tenant_users"):
        raise PermissionDeniedError()

    rows = session.execute(
        select(TenantUser).where(TenantUser.tenant_id == tenant_id)
    ).scalars().all()

    return [
        BarberLinkOut(
            user_id=str(u.id),
            email=u.email,
            barber_id=str(u.barber_id) if u.barber_id else None,
            barber_name=u.barber.name if u.barber else None,
        )
        for u in rows
    ]


@router.put("/{user_id}", response_model=BarberLinkOut)
def update_barber_link(
    user_id: UUID,
    payload: BarberLinkUpdate,
    tenant_id: UUID = Depends(require_tenant),
    session: Session = Depends(get_session),
    principal=Depends(get_tenant_principal),
) -> BarberLinkOut:
    """Link or unlink a tenant user to a barber."""
    if not check_permission(principal, "manage_tenant_users"):
        raise PermissionDeniedError()

    user = session.execute(
        select(TenantUser).where(
            TenantUser.id == user_id,
            TenantUser.tenant_id == tenant_id,
        )
    ).scalar_one_or_none()

    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    if payload.barber_id is not None:
        barber = session.execute(
            select(Barber).where(
                Barber.id == payload.barber_id,
                Barber.tenant_id == tenant_id,
            )
        ).scalar_one_or_none()
        if barber is None:
            raise HTTPException(
                status_code=404,
                detail=f"Barber {payload.barber_id} not found for this tenant",
            )
        user.barber_id = payload.barber_id
    else:
        user.barber_id = None

    session.flush()
    session.commit()

    audit = TenantAuditLogRepository(session, tenant_id)
    audit.log(
        event_type="barber_link_updated",
        level="info",
        message=f"User '{user.email}' linked to barber_id={payload.barber_id}",
        actor_scope="tenant",
        actor_id=str(principal.user_id) if principal else None,
    )
    session.commit()

    barber_name = None
    if user.barber:
        barber_name = user.barber.name

    return BarberLinkOut(
        user_id=str(user.id),
        email=user.email,
        barber_id=str(user.barber_id) if user.barber_id else None,
        barber_name=barber_name,
    )

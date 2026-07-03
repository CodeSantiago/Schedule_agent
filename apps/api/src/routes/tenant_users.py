"""Tenant user management endpoints.

Allows tenant owners to manage users within their tenant and superadmins
to manage users across all tenants.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.src.deps import (
    get_auth_service,
    get_current_principal,
    get_session,
    get_tenant_principal,
    require_tenant,
    tenant_id_from_path,
)
from packages.application.auth import AuthService, AuthError
from packages.application.auth.rbac import (
    VALID_ROLES,
    check_permission,
    PermissionDeniedError,
)
from packages.infrastructure.db.models.scheduling import Barber
from packages.infrastructure.db.models.tenant_user import TenantUser
from packages.infrastructure.repositories import TenantAuditLogRepository

tenant_router = APIRouter(
    prefix="/tenants/{tenant_id}/users",
    tags=["tenant-users"],
)
superadmin_router = APIRouter(
    prefix="/superadmin/tenants/{tenant_id}/users",
    tags=["tenant-users"],
)


class TenantUserOut(BaseModel):
    id: str
    email: str
    name: str
    role: str
    is_active: bool
    barber_id: str | None = None
    barber_name: str | None = None


class TenantUserCreate(BaseModel):
    email: str
    password: str
    name: str
    role: str = "staff"
    barber_id: UUID | None = None


class TenantUserUpdate(BaseModel):
    name: str | None = None
    role: str | None = None
    is_active: bool | None = None
    barber_id: UUID | None = None


TENANT_ASSIGNABLE_ROLES = frozenset({"staff", "barber"})


def _validate_tenant_assignable_role(role: str) -> None:
    if role not in TENANT_ASSIGNABLE_ROLES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid role '{role}'. Tenant owners can assign only: "
                f"{', '.join(sorted(TENANT_ASSIGNABLE_ROLES))}"
            ),
        )


def _resolve_barber_link(session: Session, tenant_id: UUID, barber_id: UUID | None) -> Barber | None:
    if barber_id is None:
        return None
    barber = session.execute(
        select(Barber).where(
            Barber.id == barber_id,
            Barber.tenant_id == tenant_id,
        )
    ).scalar_one_or_none()
    if barber is None:
        raise HTTPException(status_code=404, detail="Barber not found")
    return barber


# ── Tenant self-service ──────────────────────────────────────────────────


@tenant_router.get("", response_model=list[TenantUserOut])
def list_tenant_users(
    tenant_id: UUID = Depends(require_tenant),
    session: Session = Depends(get_session),
    principal=Depends(get_tenant_principal),
) -> list[TenantUserOut]:
    """List all users in the tenant (requires manage_tenant_users permission)."""
    if not check_permission(principal, "manage_tenant_users"):
        raise PermissionDeniedError()

    rows = session.execute(
        select(TenantUser).where(TenantUser.tenant_id == tenant_id).order_by(TenantUser.created_at)
    ).scalars().all()
    return [
        TenantUserOut(
            id=str(r.id),
            email=r.email,
            name=r.name,
            role=r.role,
            is_active=r.is_active == "true",
            barber_id=str(r.barber_id) if r.barber_id else None,
            barber_name=r.barber.name if r.barber else None,
        )
        for r in rows
    ]


@tenant_router.post("", response_model=TenantUserOut, status_code=status.HTTP_201_CREATED)
def create_tenant_user(
    payload: TenantUserCreate,
    tenant_id: UUID = Depends(require_tenant),
    session: Session = Depends(get_session),
    auth_svc: AuthService = Depends(get_auth_service),
    principal=Depends(get_tenant_principal),
) -> TenantUserOut:
    """Create a new user in the tenant (owner role only)."""
    if not check_permission(principal, "manage_tenant_users"):
        raise PermissionDeniedError()
    _validate_tenant_assignable_role(payload.role)

    barber = _resolve_barber_link(session, tenant_id, payload.barber_id)
    if payload.role == "barber" and barber is None:
        raise HTTPException(status_code=400, detail="barber role requires a linked barber_id")

    try:
        user = auth_svc.create_tenant_user(
            tenant_id=tenant_id,
            email=payload.email,
            password=payload.password,
            name=payload.name,
            activate=True,
        )
    except AuthError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    # Override default role.
    if user.role != payload.role:
        user.role = payload.role
    user.barber_id = barber.id if barber is not None else None
    session.flush()

    session.commit()

    audit = TenantAuditLogRepository(session, tenant_id)
    audit.log(
        event_type="user_created",
        level="info",
        message=f"Tenant user '{payload.email}' created with role '{payload.role}'",
        actor_scope="tenant",
        actor_id=str(principal.user_id) if principal else None,
    )
    session.commit()

    return TenantUserOut(
        id=str(user.id),
        email=user.email,
        name=user.name,
        role=user.role,
        is_active=user.is_active == "true",
        barber_id=str(user.barber_id) if user.barber_id else None,
        barber_name=user.barber.name if user.barber else None,
    )


@tenant_router.patch("/{user_id}", response_model=TenantUserOut)
def update_tenant_user(
    user_id: UUID,
    payload: TenantUserUpdate,
    tenant_id: UUID = Depends(require_tenant),
    session: Session = Depends(get_session),
    principal=Depends(get_tenant_principal),
) -> TenantUserOut:
    """Update a tenant user's name, role, or active status (owner only)."""
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

    if principal and str(principal.user_id) == str(user.id) and payload.role is not None:
        if payload.role != user.role:
            raise HTTPException(
                status_code=400,
                detail="you cannot change your own role from this screen",
            )

    changed: dict = {}
    if payload.name is not None:
        user.name = payload.name.strip()
        changed["name"] = True
    if payload.role is not None:
        _validate_tenant_assignable_role(payload.role)
        user.role = payload.role
        changed["role"] = payload.role
    if payload.is_active is not None:
        user.is_active = "true" if payload.is_active else "false"
        changed["is_active"] = payload.is_active

    effective_role = payload.role if payload.role is not None else user.role
    if payload.barber_id is not None or effective_role == "barber":
        barber = _resolve_barber_link(session, tenant_id, payload.barber_id)
        if effective_role == "barber" and barber is None:
            raise HTTPException(status_code=400, detail="barber role requires a linked barber_id")
        user.barber_id = barber.id if barber is not None else None
        changed["barber_id"] = str(user.barber_id) if user.barber_id else None
    elif effective_role != "barber" and user.barber_id is not None:
        user.barber_id = None
        changed["barber_id"] = None

    session.flush()
    session.commit()

    audit = TenantAuditLogRepository(session, tenant_id)
    audit.log(
        event_type="user_updated",
        level="info",
        message=f"Tenant user '{user.email}' updated",
        actor_scope="tenant",
        actor_id=str(principal.user_id) if principal else None,
        changed_fields=changed,
    )
    session.commit()

    return TenantUserOut(
        id=str(user.id),
        email=user.email,
        name=user.name,
        role=user.role,
        is_active=user.is_active == "true",
        barber_id=str(user.barber_id) if user.barber_id else None,
        barber_name=user.barber.name if user.barber else None,
    )


# ── Superadmin ───────────────────────────────────────────────────────────


@superadmin_router.get("", response_model=list[TenantUserOut])
def list_superadmin_users(
    tenant_id: UUID = Depends(tenant_id_from_path),
    session: Session = Depends(get_session),
    _principal=Depends(get_current_principal),
) -> list[TenantUserOut]:
    """List all users in a tenant (superadmin view)."""
    rows = session.execute(
        select(TenantUser).where(TenantUser.tenant_id == tenant_id).order_by(TenantUser.created_at)
    ).scalars().all()
    return [
        TenantUserOut(
            id=str(r.id),
            email=r.email,
            name=r.name,
            role=r.role,
            is_active=r.is_active == "true",
        )
        for r in rows
    ]


@superadmin_router.post("", response_model=TenantUserOut, status_code=status.HTTP_201_CREATED)
def create_superadmin_user(
    tenant_id: UUID = Depends(tenant_id_from_path),
    payload: TenantUserCreate = ...,
    session: Session = Depends(get_session),
    auth_svc: AuthService = Depends(get_auth_service),
    principal=Depends(get_current_principal),
) -> TenantUserOut:
    """Create a user in a tenant (superadmin)."""
    if payload.role not in VALID_ROLES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid role '{payload.role}'. Valid roles: {', '.join(sorted(VALID_ROLES))}",
        )
    try:
        user = auth_svc.create_tenant_user(
            tenant_id=tenant_id,
            email=payload.email,
            password=payload.password,
            name=payload.name,
            activate=True,
        )
    except AuthError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if user.role != payload.role:
        user.role = payload.role
        session.flush()

    session.commit()
    return TenantUserOut(
        id=str(user.id),
        email=user.email,
        name=user.name,
        role=user.role,
        is_active=user.is_active == "true",
    )

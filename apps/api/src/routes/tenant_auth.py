"""Tenant user auth endpoints.

These routes let tenant users authenticate and inspect their own
principal. They sit under `/tenants/auth/...` and return the same
`LoginResponse` shape as the superadmin auth endpoint.

Only `POST /tenants/auth/login` is unauthenticated — the rest require
a valid tenant-scoped bearer token.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from apps.api.src.deps import (
    get_auth_service,
    get_current_principal,
    get_session,
)
from apps.api.src.schemas import LoginRequest, LoginResponse
from packages.application.auth import AuthError, AuthService, Principal
from packages.infrastructure.db.models.tenant_user import TenantUser
from sqlalchemy import select

router = APIRouter(prefix="/tenants/auth", tags=["tenants"])


class TenantMeResponse(LoginResponse):
    """Login response extended with barber info when the user is linked."""
    barber_id: str | None = None
    barber_name: str | None = None
    name: str | None = None
    photo_url: str | None = None


class TenantProfileUpdate(BaseModel):
    name: str | None = None
    photo_url: str | None = None


@router.post("/login", response_model=LoginResponse)
def login(
    payload: LoginRequest,
    service: AuthService = Depends(get_auth_service),
    session: Session = Depends(get_session),
) -> LoginResponse:
    try:
        issued = service.authenticate_tenant(
            email=payload.email,
            password=payload.password,
            label=(payload.label or "").strip(),
        )
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    principal = service.verify_bearer(issued.raw)
    session.commit()
    return LoginResponse(
        token=issued.raw,
        token_prefix=issued.prefix,
        principal_id=principal.user_id,
        email=principal.email,
        scope=principal.scope,
        tenant_id=principal.tenant_id,
    )


@router.get("/me", response_model=TenantMeResponse)
def me(
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
) -> TenantMeResponse:
    """Return the current principal's info, including linked barber info.

    Requires a valid bearer token (any scope). The response mirrors
    the login response shape so the frontend can use the same parser.
    """
    barber_id = None
    barber_name = None
    display_name = None
    photo_url = None

    if principal.scope == "tenant" and principal.user_id:
        user = session.execute(
            select(TenantUser).where(TenantUser.id == principal.user_id)
        ).scalar_one_or_none()
        if user and user.barber_id:
            barber_id = str(user.barber_id)
            barber_name = user.barber.name if user.barber else None
        if user is not None:
            display_name = user.name
            photo_url = user.photo_url

    return TenantMeResponse(
        token="",
        token_prefix="",
        principal_id=principal.user_id,
        email=principal.email,
        scope=principal.scope,
        tenant_id=principal.tenant_id,
        role=principal.role,
        barber_id=barber_id,
        barber_name=barber_name,
        name=display_name,
        photo_url=photo_url,
    )


@router.patch("/me", response_model=TenantMeResponse)
def update_me(
    payload: TenantProfileUpdate,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
) -> TenantMeResponse:
    if principal.scope != "tenant" or not principal.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="this endpoint requires a tenant-scoped token",
        )

    user = session.execute(
        select(TenantUser).where(TenantUser.id == principal.user_id)
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")

    if payload.name is not None:
        cleaned_name = payload.name.strip()
        if not cleaned_name:
            raise HTTPException(status_code=400, detail="name cannot be empty")
        user.name = cleaned_name[:120]

    if payload.photo_url is not None:
        cleaned_url = payload.photo_url.strip()
        if cleaned_url and not (
            cleaned_url.startswith("http://") or cleaned_url.startswith("https://")
        ):
            raise HTTPException(
                status_code=400,
                detail="photo_url must start with http:// or https://",
            )
        user.photo_url = cleaned_url[:500] or None

    session.commit()

    barber_id = str(user.barber_id) if user.barber_id else None
    barber_name = user.barber.name if user.barber else None
    return TenantMeResponse(
        token="",
        token_prefix="",
        principal_id=principal.user_id,
        email=principal.email,
        scope=principal.scope,
        tenant_id=principal.tenant_id,
        role=principal.role,
        barber_id=barber_id,
        barber_name=barber_name,
        name=user.name,
        photo_url=user.photo_url,
    )


@router.post("/logout")
def tenant_logout(
    request: Request,
    service: AuthService = Depends(get_auth_service),
    session: Session = Depends(get_session),
) -> dict:
    """Revoke the current bearer token (tenant logout).

    Reads the token from the Authorization header and revokes it.
    The caller should discard the token on the client side.
    """
    header = request.headers.get("authorization") or request.headers.get(
        "Authorization"
    )
    if header:
        parts = header.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            service.revoke(parts[1].strip())
            session.commit()
    return {"ok": True, "message": "signed out"}

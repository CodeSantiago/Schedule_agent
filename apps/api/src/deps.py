"""FastAPI dependencies.

These exist so routes don't have to know about session lifetime, repo
construction, or how to translate domain UUIDs into ORM rows. They also
keep the route handlers thin and the test surface small.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Path, Request, status
from sqlalchemy.orm import Session

from packages.application.auth import AuthService
from packages.application.providers import ProviderConfigService
from packages.application.scheduling.booking_service import BookingService
from packages.application.scheduling.manage_service import (
    AppointmentManageService,
)
from packages.application.scheduling.overview_service import OverviewService
from packages.application.superadmin import SuperadminTenantService
from packages.infrastructure.db.session import get_db
from packages.infrastructure.repositories import (
    AbsenceRepository,
    AppointmentRepository,
    BarberRepository,
    ExtraHourRepository,
    ProviderConfigRepository,
    ScheduleRepository,
    ServiceRepository,
    TenantRepository,
)


def get_session(db: Session = Depends(get_db)) -> Session:
    return db


def tenant_id_from_path(
    tenant_id: UUID = Path(..., description="Tenant UUID"),
) -> UUID:
    return tenant_id


def get_tenant_repo(
    session: Session = Depends(get_session),
    tenant_id: UUID = Depends(tenant_id_from_path),
) -> TenantRepository:
    return TenantRepository(session, tenant_id)


def get_barber_repo(
    session: Session = Depends(get_session),
    tenant_id: UUID = Depends(tenant_id_from_path),
) -> BarberRepository:
    return BarberRepository(session, tenant_id)


def get_service_repo(
    session: Session = Depends(get_session),
    tenant_id: UUID = Depends(tenant_id_from_path),
) -> ServiceRepository:
    return ServiceRepository(session, tenant_id)


def get_schedule_repo(
    session: Session = Depends(get_session),
    tenant_id: UUID = Depends(tenant_id_from_path),
) -> ScheduleRepository:
    return ScheduleRepository(session, tenant_id)


def get_absence_repo(
    session: Session = Depends(get_session),
    tenant_id: UUID = Depends(tenant_id_from_path),
) -> AbsenceRepository:
    return AbsenceRepository(session, tenant_id)


def get_extra_hour_repo(
    session: Session = Depends(get_session),
    tenant_id: UUID = Depends(tenant_id_from_path),
) -> ExtraHourRepository:
    return ExtraHourRepository(session, tenant_id)


def get_appointment_repo(
    session: Session = Depends(get_session),
    tenant_id: UUID = Depends(tenant_id_from_path),
) -> AppointmentRepository:
    return AppointmentRepository(session, tenant_id)


def get_booking_service(
    session: Session = Depends(get_session),
    tenant_id: UUID = Depends(tenant_id_from_path),
) -> BookingService:
    return BookingService(session, tenant_id)


def get_manage_service(
    session: Session = Depends(get_session),
    tenant_id: UUID = Depends(tenant_id_from_path),
) -> AppointmentManageService:
    return AppointmentManageService(session, tenant_id)


def get_overview_service(
    session: Session = Depends(get_session),
    tenant_id: UUID = Depends(tenant_id_from_path),
) -> OverviewService:
    return OverviewService(session, tenant_id)


def get_provider_config_repo(
    session: Session = Depends(get_session),
    tenant_id: UUID = Depends(tenant_id_from_path),
) -> ProviderConfigRepository:
    return ProviderConfigRepository(session, tenant_id)


def get_provider_config_service(
    session: Session = Depends(get_session),
    tenant_id: UUID = Depends(tenant_id_from_path),
) -> ProviderConfigService:
    return ProviderConfigService(session, tenant_id)


def get_superadmin_tenant_service(
    session: Session = Depends(get_session),
) -> SuperadminTenantService:
    return SuperadminTenantService(session)


def get_auth_service(
    session: Session = Depends(get_session),
) -> AuthService:
    return AuthService(session)


# --- Auth-protected routes ------------------------------------------------


def get_current_principal(
    request: Request,
    service: AuthService = Depends(get_auth_service),
) -> "object":
    """Resolve the bearer token from the `Authorization` header.

    Returns a `Principal` on success. Raises `HTTPException(401)` when
    the header is missing, malformed, or the token is unknown/revoked.
    """
    from packages.application.auth import AuthError, Principal

    header = request.headers.get("authorization") or request.headers.get(
        "Authorization"
    )
    if not header:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    parts = header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return service.verify_bearer(parts[1].strip())
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


# --- Tenant-scoped auth dependencies ----------------------------------------


def get_tenant_principal(
    principal: "object" = Depends(get_current_principal),
) -> "object":
    """Like `get_current_principal` but also requires scope `"tenant"`.

    Raises 403 when the token is valid but belongs to a superadmin
    (scope mismatch).
    """
    from packages.application.auth import Principal as P

    if not isinstance(principal, P) or principal.scope != "tenant":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="this endpoint requires a tenant-scoped token",
        )
    return principal


def require_tenant(
    tenant_id: TenantPath,
    principal: "object" = Depends(get_tenant_principal),
) -> UUID:
    """Resolve `tenant_id` from the path AND verify the principal belongs
    to that tenant.

    Returns the `tenant_id` on success. Raises 403 when the principal's
    tenant does not match the path parameter.
    """
    from packages.application.auth import Principal as P

    if isinstance(principal, P) and principal.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="principal does not belong to this tenant",
        )
    return tenant_id


# Common path-style annotation to keep route signatures tidy.
TenantPath = Annotated[UUID, Path(..., description="Tenant UUID")]

"""Superadmin endpoints (cross-tenant).

These are the only routes that operate across tenants. They sit under
`/superadmin/...` and require a valid superadmin bearer token. The
auth primitive is the same `AuthService.verify_bearer` used
elsewhere; the dependency `get_current_principal` raises 401 for any
missing/invalid token.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from apps.api.src.deps import (
    get_auth_service,
    get_current_principal,
    get_session,
    get_superadmin_tenant_service,
)
from apps.api.src.schemas import (
    LoginRequest,
    LoginResponse,
    SuperadminTenantCreate,
    SuperadminTenantOut,
    SuperadminTenantStatusUpdate,
    TenantBotConfigOut,
    TenantBotConfigUpdate,
    TenantOperationalSettingsOut,
    TenantOperationalSettingsUpdate,
)
from packages.application.auth import AuthError, AuthService, Principal
from packages.application.superadmin import SuperadminTenantService
from packages.infrastructure.repositories import TenantAuditLogRepository, TenantRepository

router = APIRouter(prefix="/superadmin", tags=["superadmin"])


# --- Auth -----------------------------------------------------------------


@router.post("/auth/login", response_model=LoginResponse)
def login(
    payload: LoginRequest,
    service: AuthService = Depends(get_auth_service),
    session: Session = Depends(get_session),
) -> LoginResponse:
    try:
        issued = service.authenticate(
            email=payload.email,
            password=payload.password,
            label=(payload.label or "").strip(),
        )
    except AuthError as exc:
        # Constant-time guard: every auth failure is the same status code
        # and the same message, so probing the endpoint cannot enumerate
        # which emails are registered.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    # Look up the principal to return its id + email in the response.
    # We could embed both in the token, but a second short query keeps
    # the token payload minimal.
    principal = service.verify_bearer(issued.raw)
    session.commit()
    return LoginResponse(
        token=issued.raw,
        token_prefix=issued.prefix,
        principal_id=principal.user_id,
        email=principal.email,
        scope=principal.scope,
    )


# --- Tenant management ----------------------------------------------------


@router.get("/tenants", response_model=list[SuperadminTenantOut])
def list_tenants(
    status_filter: Annotated[
        str | None,
        Query(alias="status", description="Filter by tenant status (e.g. trial)"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    svc: SuperadminTenantService = Depends(get_superadmin_tenant_service),
    _principal=Depends(get_current_principal),
) -> list[SuperadminTenantOut]:
    rows = svc.list_tenants(status=status_filter, limit=limit, offset=offset)
    return [SuperadminTenantOut.model_validate(r) for r in rows]


@router.post(
    "/tenants",
    response_model=SuperadminTenantOut,
    status_code=status.HTTP_201_CREATED,
)
def create_tenant(
    payload: SuperadminTenantCreate,
    svc: SuperadminTenantService = Depends(get_superadmin_tenant_service),
    session: Session = Depends(get_session),
    _principal=Depends(get_current_principal),
) -> SuperadminTenantOut:
    try:
        tenant = svc.create_tenant(
            name=payload.name,
            slug=payload.slug,
            timezone=payload.timezone,
            status=payload.status,
            initial_settings=payload.initial_settings,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    session.commit()
    return SuperadminTenantOut.model_validate(tenant)


# --- Tenant operational settings (superadmin) ----------------------------


def _read_ops_config(session: Session, tenant_id: UUID) -> dict:
    """Return the FULL config dict with ops-key defaults applied.

    Preserves every existing key so unrelated config sections
    (bot.greeting_text, business.*, etc.) survive a write-back.
    """
    repo = TenantRepository(session, tenant_id)
    settings = repo.get_settings()
    raw = dict(settings.config) if settings else {}
    bot = raw.get("bot")
    if not isinstance(bot, dict):
        bot = {}
        raw["bot"] = bot
    bot.setdefault("enabled", True)
    booking = raw.get("booking")
    if not isinstance(booking, dict):
        booking = {}
        raw["booking"] = booking
    booking.setdefault("closed_dates", [])
    return raw


@router.get(
    "/tenants/{tenant_id}/settings/operations",
    response_model=TenantOperationalSettingsOut,
)
def get_tenant_operations(
    tenant_id: UUID,
    session: Session = Depends(get_session),
    _principal=Depends(get_current_principal),
) -> TenantOperationalSettingsOut:
    """Return a tenant's operational settings (superadmin view).
    Missing settings rows are filled with defaults.
    """
    config = _read_ops_config(session, tenant_id)
    return TenantOperationalSettingsOut(
        bot_enabled=config["bot"]["enabled"],
        closed_dates=config["booking"]["closed_dates"],
    )


@router.put(
    "/tenants/{tenant_id}/settings/operations",
    response_model=TenantOperationalSettingsOut,
)
def update_tenant_operations(
    tenant_id: UUID,
    payload: TenantOperationalSettingsUpdate,
    session: Session = Depends(get_session),
    principal=Depends(get_current_principal),
) -> TenantOperationalSettingsOut:
    """Update a tenant's operational settings (superadmin).
    Only the provided fields are changed; omitted fields keep their
    current value. The change is recorded in the audit log.
    """
    config = _read_ops_config(session, tenant_id)
    changed: dict = {}
    if payload.bot_enabled is not None:
        config["bot"]["enabled"] = payload.bot_enabled
        changed["bot_enabled"] = payload.bot_enabled
    if payload.closed_dates is not None:
        config["booking"]["closed_dates"] = payload.closed_dates
        changed["closed_dates_count"] = len(payload.closed_dates)
    repo = TenantRepository(session, tenant_id)
    repo.upsert_settings(config)
    session.commit()

    # Audit log.
    audit = TenantAuditLogRepository(session, tenant_id)
    audit.log(
        event_type="settings_updated",
        level="info",
        message=f"Operational settings updated by superadmin",
        actor_scope="superadmin",
        actor_id=str(principal.user_id) if hasattr(principal, "user_id") else None,
        changed_fields=changed,
    )
    session.commit()
    return TenantOperationalSettingsOut(
        bot_enabled=config["bot"]["enabled"],
        closed_dates=config["booking"]["closed_dates"],
    )


# --- Bot + business config (superadmin) ------------------------------------


def _read_bot_config(session: Session, tenant_id: UUID) -> dict:
    """Return the FULL config dict with bot/business-key defaults applied."""
    repo = TenantRepository(session, tenant_id)
    settings = repo.get_settings()
    raw = dict(settings.config) if settings else {}
    bot = raw.get("bot")
    if not isinstance(bot, dict):
        bot = {}
        raw["bot"] = bot
    bot.setdefault("greeting_text", "")
    bot.setdefault("behavior_notes", "")
    business = raw.get("business")
    if not isinstance(business, dict):
        business = {}
        raw["business"] = business
    business.setdefault("display_name", "")
    business.setdefault("contact_phone", "")
    business.setdefault("booking_notes", "")
    business.setdefault("location", "")
    business.setdefault("hours", "")
    return raw


@router.get(
    "/tenants/{tenant_id}/settings/config",
    response_model=TenantBotConfigOut,
)
def get_tenant_config(
    tenant_id: UUID,
    session: Session = Depends(get_session),
    _principal=Depends(get_current_principal),
) -> TenantBotConfigOut:
    """Return a tenant's bot + business config (superadmin view)."""
    config = _read_bot_config(session, tenant_id)
    bot = config.get("bot", {})
    business = config.get("business", {})
    return TenantBotConfigOut(
        greeting_text=bot.get("greeting_text", ""),
        behavior_notes=bot.get("behavior_notes", ""),
        display_name=business.get("display_name", ""),
        contact_phone=business.get("contact_phone", ""),
        booking_notes=business.get("booking_notes", ""),
        location=business.get("location", ""),
        hours=business.get("hours", ""),
    )


@router.put(
    "/tenants/{tenant_id}/settings/config",
    response_model=TenantBotConfigOut,
)
def update_tenant_config(
    tenant_id: UUID,
    payload: TenantBotConfigUpdate,
    session: Session = Depends(get_session),
    principal=Depends(get_current_principal),
) -> TenantBotConfigOut:
    """Update a tenant's bot + business config (superadmin).

    Only the provided fields are changed; omitted fields keep their
    current value. The change is recorded in the audit log.
    """
    config = _read_bot_config(session, tenant_id)
    changed: dict = {}

    if payload.greeting_text is not None:
        config.setdefault("bot", {})["greeting_text"] = payload.greeting_text
        changed["greeting_text"] = True
    if payload.behavior_notes is not None:
        config.setdefault("bot", {})["behavior_notes"] = payload.behavior_notes
        changed["behavior_notes"] = True
    if payload.display_name is not None:
        config.setdefault("business", {})["display_name"] = payload.display_name
        changed["display_name"] = True
    if payload.contact_phone is not None:
        config.setdefault("business", {})["contact_phone"] = payload.contact_phone
        changed["contact_phone"] = True
    if payload.booking_notes is not None:
        config.setdefault("business", {})["booking_notes"] = payload.booking_notes
        changed["booking_notes"] = True
    if payload.location is not None:
        config.setdefault("business", {})["location"] = payload.location
        changed["location"] = True
    if payload.hours is not None:
        config.setdefault("business", {})["hours"] = payload.hours
        changed["hours"] = True

    repo = TenantRepository(session, tenant_id)
    repo.upsert_settings(config)
    session.commit()

    # Audit log.
    audit = TenantAuditLogRepository(session, tenant_id)
    audit.log(
        event_type="settings_updated",
        level="info",
        message=f"Bot/business config updated by superadmin",
        actor_scope="superadmin",
        actor_id=str(principal.user_id) if hasattr(principal, "user_id") else None,
        changed_fields=changed,
    )
    session.commit()

    bot = config.get("bot", {})
    business = config.get("business", {})
    return TenantBotConfigOut(
        greeting_text=bot.get("greeting_text", ""),
        behavior_notes=bot.get("behavior_notes", ""),
        display_name=business.get("display_name", ""),
        contact_phone=business.get("contact_phone", ""),
        booking_notes=business.get("booking_notes", ""),
        location=business.get("location", ""),
        hours=business.get("hours", ""),
    )


@router.get("/tenants/{tenant_id}", response_model=SuperadminTenantOut)
def get_tenant(
    tenant_id: UUID,
    svc: SuperadminTenantService = Depends(get_superadmin_tenant_service),
    _principal=Depends(get_current_principal),
) -> SuperadminTenantOut:
    tenant = svc.get(tenant_id)
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"tenant {tenant_id} not found",
        )
    return SuperadminTenantOut.model_validate(tenant)


@router.patch(
    "/tenants/{tenant_id}/status",
    response_model=SuperadminTenantOut,
)
def update_tenant_status(
    tenant_id: UUID,
    payload: SuperadminTenantStatusUpdate,
    svc: SuperadminTenantService = Depends(get_superadmin_tenant_service),
    session: Session = Depends(get_session),
    _principal=Depends(get_current_principal),
) -> SuperadminTenantOut:
    tenant = svc.update_status(tenant_id, payload.status)
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"tenant {tenant_id} not found",
        )
    session.commit()
    return SuperadminTenantOut.model_validate(tenant)


@router.delete(
    "/tenants/{tenant_id}",
    response_model=SuperadminTenantOut,
)
def soft_delete_tenant(
    tenant_id: UUID,
    svc: SuperadminTenantService = Depends(get_superadmin_tenant_service),
    session: Session = Depends(get_session),
    _principal=Depends(get_current_principal),
) -> SuperadminTenantOut:
    """Soft-delete a tenant by flipping its status to `churned`.

    No rows are physically removed; the tenant row, its settings, and
    all child rows (barbers, appointments, provider configs) stay in
    the database. The action is idempotent — calling it on an
    already-churned tenant returns 200 with the unchanged row. Use
    the `PATCH /status` endpoint to reactivate.
    """
    tenant = svc.soft_delete(tenant_id)
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"tenant {tenant_id} not found",
        )
    session.commit()
    return SuperadminTenantOut.model_validate(tenant)

"""Tenant self-service operational settings endpoints.

These routes let a tenant user read and update their own operational
settings (bot enabled/disabled and booking closed dates). They sit
under ``/tenants/{tenant_id}/settings/operations`` and require a
valid tenant-scoped bearer token.

Superadmin equivalents live in ``superadmin.py`` under
``/superadmin/tenants/{tenant_id}/settings/operations``.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from apps.api.src.deps import (
    get_session,
    get_tenant_principal,
    get_tenant_repo,
    require_tenant,
)
from apps.api.src.schemas import (
    TenantOperationalSettingsOut,
    TenantOperationalSettingsUpdate,
)
from packages.infrastructure.repositories import TenantAuditLogRepository, TenantRepository

router = APIRouter(
    prefix="/tenants/{tenant_id}/settings/operations",
    tags=["operations"],
)


# ── Calendar override schemas ─────────────────────────────────────────────


class HolidayRange(BaseModel):
    """A date range where the barbershop is closed (holiday/closure)."""

    start: date
    end: date
    reason: str | None = None


class BarberOverride(BaseModel):
    """Temporary override for a barber's availability."""

    barber_id: str
    date_from: date | None = None
    date_to: date | None = None
    is_disabled: bool = True
    reason: str | None = None


class CalendarOverridesOut(BaseModel):
    holiday_ranges: list[HolidayRange]
    barber_overrides: list[BarberOverride]
    closed_dates: list[str]


class CalendarOverridesUpdate(BaseModel):
    holiday_ranges: list[HolidayRange] | None = None
    barber_overrides: list[BarberOverride] | None = None
    closed_dates: list[str] | None = None


def _default_ops() -> dict:
    return {"bot": {"enabled": True}, "booking": {"closed_dates": [], "holiday_ranges": [], "barber_overrides": []}}


def _read_ops_config(repo: TenantRepository) -> dict:
    """Return the FULL config dict, ensuring ops keys have defaults.

    Preserves every existing key so that unrelated config sections
    (bot.greeting_text, business.*, etc.) are NOT lost when the
    caller writes back via ``upsert_settings``.
    """
    settings = repo.get_settings()
    raw = dict(settings.config) if settings else {}
    # Ensure bot key exists with defaults.
    bot = raw.get("bot")
    if not isinstance(bot, dict):
        bot = {}
        raw["bot"] = bot
    bot.setdefault("enabled", True)
    # Ensure booking key exists with defaults.
    booking = raw.get("booking")
    if not isinstance(booking, dict):
        booking = {}
        raw["booking"] = booking
    booking.setdefault("closed_dates", [])
    booking.setdefault("holiday_ranges", [])
    booking.setdefault("barber_overrides", [])
    return raw


@router.get("", response_model=TenantOperationalSettingsOut)
def get_operations(
    repo: TenantRepository = Depends(get_tenant_repo),
    tenant_id: UUID = Depends(require_tenant),
) -> TenantOperationalSettingsOut:
    """Return the tenant's operational settings.

    Always returns a well-formed response (never 404) — missing
    settings rows are filled with defaults.
    """
    config = _read_ops_config(repo)
    return TenantOperationalSettingsOut(
        bot_enabled=config["bot"]["enabled"],
        closed_dates=config["booking"]["closed_dates"],
    )


@router.put("", response_model=TenantOperationalSettingsOut)
def update_operations(
    payload: TenantOperationalSettingsUpdate,
    repo: TenantRepository = Depends(get_tenant_repo),
    session: Session = Depends(get_session),
    tenant_id: UUID = Depends(require_tenant),
    principal=Depends(get_tenant_principal),
) -> TenantOperationalSettingsOut:
    """Update the tenant's operational settings.

    Only the provided fields are changed; omitted fields keep their
    current value. The change is recorded in the audit log.
    """
    config = _read_ops_config(repo)

    changed: dict = {}
    if payload.bot_enabled is not None:
        config["bot"]["enabled"] = payload.bot_enabled
        changed["bot_enabled"] = payload.bot_enabled
    if payload.closed_dates is not None:
        config["booking"]["closed_dates"] = payload.closed_dates
        changed["closed_dates_count"] = len(payload.closed_dates)

    repo.upsert_settings(config)
    session.commit()

    # Audit log.
    audit = TenantAuditLogRepository(session, tenant_id)
    audit.log(
        event_type="settings_updated",
        level="info",
        message=f"Operational settings updated by tenant",
        actor_scope="tenant",
        actor_id=str(principal.user_id) if principal else None,
        changed_fields=changed,
    )
    session.commit()

    return TenantOperationalSettingsOut(
        bot_enabled=config["bot"]["enabled"],
        closed_dates=config["booking"]["closed_dates"],
    )


# ── Calendar overrides (holiday ranges + barber overrides) ──────────────


@router.get("/calendar", response_model=CalendarOverridesOut)
def get_calendar_overrides(
    repo: TenantRepository = Depends(get_tenant_repo),
    tenant_id: UUID = Depends(require_tenant),
) -> CalendarOverridesOut:
    """Return calendar overrides: holiday ranges, barber overrides, closed dates."""
    config = _read_ops_config(repo)
    booking = config.get("booking", {})
    return CalendarOverridesOut(
        holiday_ranges=[HolidayRange(**r) for r in booking.get("holiday_ranges", [])],
        barber_overrides=[BarberOverride(**r) for r in booking.get("barber_overrides", [])],
        closed_dates=booking.get("closed_dates", []),
    )


@router.put("/calendar", response_model=CalendarOverridesOut)
def update_calendar_overrides(
    payload: CalendarOverridesUpdate,
    repo: TenantRepository = Depends(get_tenant_repo),
    session: Session = Depends(get_session),
    tenant_id: UUID = Depends(require_tenant),
    principal=Depends(get_tenant_principal),
) -> CalendarOverridesOut:
    """Update calendar overrides: holiday ranges and/or barber overrides."""
    config = _read_ops_config(repo)
    booking = config.setdefault("booking", {})
    changed: dict = {}

    if payload.holiday_ranges is not None:
        booking["holiday_ranges"] = [
            {
                "start": str(r.start),
                "end": str(r.end),
                "reason": r.reason,
            }
            for r in payload.holiday_ranges
        ]
        changed["holiday_ranges"] = len(payload.holiday_ranges)
    if payload.barber_overrides is not None:
        booking["barber_overrides"] = [r.model_dump() for r in payload.barber_overrides]
        changed["barber_overrides"] = len(payload.barber_overrides)
    if payload.closed_dates is not None:
        booking["closed_dates"] = payload.closed_dates
        changed["closed_dates_count"] = len(payload.closed_dates)

    repo.upsert_settings(config)
    session.commit()

    audit = TenantAuditLogRepository(session, tenant_id)
    audit.log(
        event_type="calendar_overrides_updated",
        level="info",
        message=f"Calendar overrides updated by tenant",
        actor_scope="tenant",
        actor_id=str(principal.user_id) if principal else None,
        changed_fields=changed,
    )
    session.commit()

    return CalendarOverridesOut(
        holiday_ranges=[HolidayRange(**r) for r in booking.get("holiday_ranges", [])],
        barber_overrides=[BarberOverride(**r) for r in booking.get("barber_overrides", [])],
        closed_dates=booking.get("closed_dates", []),
    )

"""Operational status / health center endpoints.

Provides a unified health surface for both tenant users and superadmins:

- Bot enabled/disabled status
- Sheets connected/not-connected
- Current data policy (source_of_truth, sync_mode)
- Quick counts (barbers, services, upcoming appointments)
- Recent operational errors/warnings from the audit log
- Provider connection health summary
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from apps.api.src.deps import (
    get_current_principal,
    get_provider_config_service,
    get_session,
    get_tenant_repo,
    require_tenant,
    tenant_id_from_path,
)
from apps.api.src.routes.data_settings import _read_data_config, _sheets_connected
from apps.api.src.routes.ops_settings import _read_ops_config
from packages.application.providers import ProviderConfigService
from packages.infrastructure.repositories import (
    BarberRepository,
    ServiceRepository,
    TenantAuditLogRepository,
    TenantRepository,
)

tenant_router = APIRouter(
    prefix="/tenants/{tenant_id}/health",
    tags=["health"],
)
superadmin_router = APIRouter(
    prefix="/superadmin/tenants/{tenant_id}/health",
    tags=["health"],
)


# ── Response models ──────────────────────────────────────────────────────


class HealthBotStatus(BaseModel):
    enabled: bool
    """Whether the bot is processing inbound messages."""

    greeting_configured: bool
    behavior_notes_set: bool
    display_name_configured: bool


class HealthSheetsStatus(BaseModel):
    connected: bool
    """Whether an active sheets provider config exists."""
    config_label: str | None = None
    spreadsheet_configured: bool = False


class HealthDataPolicy(BaseModel):
    source_of_truth: str
    sync_mode: str


class HealthCounts(BaseModel):
    active_barbers: int
    total_barbers: int
    active_services: int
    total_services: int
    upcoming_appointments: int


class HealthRecentEvent(BaseModel):
    event_type: str
    level: str
    message: str
    created_at: str


class HealthProviderSummary(BaseModel):
    kind: str
    label: str
    is_active: bool
    provider_name: str


class HealthStatusOut(BaseModel):
    """Complete health/status surface for a tenant."""

    tenant_id: str
    bot: HealthBotStatus
    sheets: HealthSheetsStatus
    data_policy: HealthDataPolicy
    counts: HealthCounts
    recent_issues: list[HealthRecentEvent]
    providers: list[HealthProviderSummary]
    overall: str  # "healthy" | "attention" | "critical"


# ── Helpers ──────────────────────────────────────────────────────────────


def _build_health(
    tenant_id: UUID,
    session: Session,
) -> HealthStatusOut:
    repo = TenantRepository(session, tenant_id)

    # Bot status.
    ops_config = _read_ops_config(repo)
    bot_enabled = ops_config.get("bot", {}).get("enabled", True)
    bot_config = _read_bot_config(repo)
    bot = HealthBotStatus(
        enabled=bot_enabled,
        greeting_configured=bool(bot_config.get("greeting_text", "")),
        behavior_notes_set=bool(bot_config.get("behavior_notes", "")),
        display_name_configured=bool(bot_config.get("display_name", "")),
    )

    # Sheets + data policy.
    provider_svc = ProviderConfigService(session, tenant_id)
    sheets_connected = _sheets_connected(tenant_id, provider_svc)
    sheets_label = None
    sheets_spreadsheet = False
    if sheets_connected:
        active_sheets = provider_svc.get_active_for_kind("sheets")
        if active_sheets:
            sheets_label = active_sheets.label
            sheets_spreadsheet = bool(
                active_sheets.settings.get("spreadsheet_id")
            )

    data_config = _read_data_config(repo)
    data_policy = HealthDataPolicy(
        source_of_truth=data_config.get("data", {}).get(
            "source_of_truth", "database"
        ),
        sync_mode=data_config.get("data", {}).get("sync_mode", "manual"),
    )

    # Counts.
    barber_repo = BarberRepository(session, tenant_id)
    service_repo = ServiceRepository(session, tenant_id)
    barbers = barber_repo.list()
    services = service_repo.list()
    counts = HealthCounts(
        active_barbers=sum(1 for b in barbers if b.is_active),
        total_barbers=len(barbers),
        active_services=sum(1 for s in services if s.is_active),
        total_services=len(services),
        upcoming_appointments=_count_upcoming(session, tenant_id),
    )

    # Recent issues (warn/error logs).
    log_repo = TenantAuditLogRepository(session, tenant_id)
    recent_logs = log_repo.list_recent(limit=20)
    issues = [
        HealthRecentEvent(
            event_type=e.event_type,
            level=e.level,
            message=e.message,
            created_at=str(e.created_at),
        )
        for e in recent_logs
        if e.level in ("warn", "error")
    ]

    # Provider summary.
    from packages.infrastructure.repositories.providers import (
        ProviderConfigRepository,
    )

    pcrepo = ProviderConfigRepository(session, tenant_id)
    all_providers = pcrepo.list()
    providers = [
        HealthProviderSummary(
            kind=p.kind,
            label=p.label,
            is_active=p.is_active,
            provider_name=p.provider_name,
        )
        for p in all_providers
    ]

    # Overall status.
    critical = any(
        e.level == "error"
        for e in recent_logs[:10]
    )
    attention = (
        not bot_enabled
        or not sheets_connected
        or not bot.display_name_configured
        or any(
            e.level == "warn"
            for e in recent_logs[:10]
        )
    )
    if critical:
        overall = "critical"
    elif attention:
        overall = "attention"
    else:
        overall = "healthy"

    return HealthStatusOut(
        tenant_id=str(tenant_id),
        bot=bot,
        sheets=HealthSheetsStatus(
            connected=sheets_connected,
            config_label=sheets_label,
            spreadsheet_configured=sheets_spreadsheet,
        ),
        data_policy=data_policy,
        counts=counts,
        recent_issues=issues,
        providers=providers,
        overall=overall,
    )


def _read_bot_config(repo: TenantRepository) -> dict:
    """Minimal bot config reader (see bot_config.py for full version)."""
    settings = repo.get_settings()
    raw = dict(settings.config) if settings else {}
    bot = raw.get("bot") or {}
    business = raw.get("business") or {}
    return {
        "greeting_text": bot.get("greeting_text", ""),
        "behavior_notes": bot.get("behavior_notes", ""),
        "display_name": business.get("display_name", ""),
    }


def _read_ops_config(repo: TenantRepository) -> dict:
    """Minimal ops config reader (see ops_settings.py for full version)."""
    settings = repo.get_settings()
    raw = dict(settings.config) if settings else {}
    bot = raw.get("bot") or {}
    booking = raw.get("booking") or {}
    return {
        "bot": {"enabled": bot.get("enabled", True)},
        "booking": {"closed_dates": booking.get("closed_dates", [])},
    }


def _read_data_config(repo: TenantRepository) -> dict:
    """Minimal data config reader (see data_settings.py for full version)."""
    settings = repo.get_settings()
    raw = dict(settings.config) if settings else {}
    data = raw.get("data") or {}
    return {
        "data": {
            "source_of_truth": data.get("source_of_truth", "database"),
            "sync_mode": data.get("sync_mode", "manual"),
        }
    }


def _count_upcoming(session: Session, tenant_id: UUID) -> int:
    """Count appointments with status pending/confirmed for today onward."""
    from sqlalchemy import select, func
    from packages.infrastructure.db.models.appointments import Appointment

    today = datetime.now(timezone.utc).date()
    stmt = (
        select(func.count(Appointment.id))
        .where(Appointment.tenant_id == tenant_id)
        .where(Appointment.appointment_date >= today)
        .where(Appointment.status.in_(["pending", "confirmed"]))
    )
    result = session.execute(stmt).scalar()
    return result or 0


# ── Routes ───────────────────────────────────────────────────────────────


@tenant_router.get("", response_model=HealthStatusOut)
def get_tenant_health(
    tenant_id: UUID = Depends(require_tenant),
    session: Session = Depends(get_session),
) -> HealthStatusOut:
    """Return the operational health/status for the current tenant."""
    return _build_health(tenant_id, session)


@superadmin_router.get("", response_model=HealthStatusOut)
def get_superadmin_health(
    tenant_id: UUID = Depends(tenant_id_from_path),
    session: Session = Depends(get_session),
    _principal=Depends(get_current_principal),
) -> HealthStatusOut:
    """Return the operational health/status for any tenant (superadmin)."""
    return _build_health(tenant_id, session)

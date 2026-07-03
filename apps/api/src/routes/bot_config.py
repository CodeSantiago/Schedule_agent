"""Tenant self-service bot and business configuration endpoints.

These routes let a tenant user read and update their own bot behavior
and business display settings. They sit under
``/tenants/{tenant_id}/settings/config`` and require a valid
tenant-scoped bearer token.

Superadmin equivalents live in ``superadmin.py`` under
``/superadmin/tenants/{tenant_id}/settings/config``.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from apps.api.src.deps import (
    get_session,
    get_tenant_principal,
    get_tenant_repo,
    require_tenant,
)
from apps.api.src.schemas import (
    TenantBotConfigOut,
    TenantBotConfigUpdate,
)
from packages.infrastructure.repositories import TenantAuditLogRepository, TenantRepository

router = APIRouter(
    prefix="/tenants/{tenant_id}/settings/config",
    tags=["config"],
)


def _read_bot_config(repo: TenantRepository) -> dict:
    """Return the FULL config dict with bot/business-key defaults applied."""
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


@router.get("", response_model=TenantBotConfigOut)
def get_config(
    repo: TenantRepository = Depends(get_tenant_repo),
    tenant_id: UUID = Depends(require_tenant),
) -> TenantBotConfigOut:
    """Return the tenant's bot + business config.

    Always returns a well-formed response (never 404) — missing
    settings rows are filled with defaults.
    """
    config = _read_bot_config(repo)
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


@router.put("", response_model=TenantBotConfigOut)
def update_config(
    payload: TenantBotConfigUpdate,
    repo: TenantRepository = Depends(get_tenant_repo),
    session: Session = Depends(get_session),
    tenant_id: UUID = Depends(require_tenant),
    principal=Depends(get_tenant_principal),
) -> TenantBotConfigOut:
    """Update the tenant's bot + business config.

    Only the provided fields are changed; omitted fields keep their
    current value. The change is recorded in the audit log.
    """
    config = _read_bot_config(repo)
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

    repo.upsert_settings(config)
    session.commit()

    # Audit log.
    audit = TenantAuditLogRepository(session, tenant_id)
    audit.log(
        event_type="settings_updated",
        level="info",
        message=f"Bot/business config updated by tenant",
        actor_scope="tenant",
        actor_id=str(principal.user_id) if principal else None,
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

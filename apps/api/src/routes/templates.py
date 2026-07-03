"""Onboarding template endpoints.

Provides predefined templates for fast tenant setup. Templates include
seed data for common barbershop configurations (services, optional
initial settings). Superadmins can apply a template when creating or
configuring a tenant.

Templates are defined in-code for this first pass. A production version
would store them in the database or a config file.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from apps.api.src.deps import (
    get_current_principal,
    get_session,
    get_superadmin_tenant_service,
    tenant_id_from_path,
)
from packages.application.superadmin import SuperadminTenantService
from packages.infrastructure.db.models.scheduling import Barber, BarberSchedule, Service
from packages.infrastructure.db.models.tenants import Tenant, TenantSetting
from packages.infrastructure.repositories import TenantAuditLogRepository

router = APIRouter(
    prefix="/superadmin/templates",
    tags=["templates"],
)


# ── Template definitions ────────────────────────────────────────────────


class TemplateService(BaseModel):
    name: str
    code: str
    duration_minutes: int
    price_cents: int
    description: str | None = None


class TemplateBarber(BaseModel):
    name: str


class TemplateSchedule(BaseModel):
    barber_name: str
    weekday: str
    start_time: str
    end_time: str


class TenantTemplate(BaseModel):
    id: str
    name: str
    description: str
    services: list[TemplateService] = []
    barbers: list[TemplateBarber] = []
    schedules: list[TemplateSchedule] = []
    initial_settings: dict | None = None


# ── Built-in templates ──────────────────────────────────────────────────

BARBERIA_TEMPLATE = TenantTemplate(
    id="barberia-clasica",
    name="Barbería Clásica",
    description="Classic barbershop with Corte, Barba, and Corte+Barba services. One barber, standard weekday schedule.",
    services=[
        TemplateService(
            name="Corte", code="C", duration_minutes=30, price_cents=3000,
            description="Classic haircut",
        ),
        TemplateService(
            name="Barba", code="B", duration_minutes=15, price_cents=1500,
            description="Beard trim & shaping",
        ),
        TemplateService(
            name="Corte y Barba", code="CB", duration_minutes=45, price_cents=4000,
            description="Haircut + beard combo",
        ),
        TemplateService(
            name="Corte Infantil", code="OTHER", duration_minutes=30, price_cents=2500,
            description="Children's haircut (under 12)",
        ),
    ],
    barbers=[
        TemplateBarber(name="Barber 1"),
    ],
    schedules=[
        TemplateSchedule(barber_name="Barber 1", weekday="mon", start_time="10:00", end_time="20:00"),
        TemplateSchedule(barber_name="Barber 1", weekday="tue", start_time="10:00", end_time="20:00"),
        TemplateSchedule(barber_name="Barber 1", weekday="wed", start_time="10:00", end_time="20:00"),
        TemplateSchedule(barber_name="Barber 1", weekday="thu", start_time="10:00", end_time="20:00"),
        TemplateSchedule(barber_name="Barber 1", weekday="fri", start_time="10:00", end_time="20:00"),
        TemplateSchedule(barber_name="Barber 1", weekday="sat", start_time="10:00", end_time="14:00"),
    ],
    initial_settings={
        "bot": {
            "enabled": True,
            "greeting_text": "¡Bienvenido! Soy el asistente virtual de la barbería. ¿En qué puedo ayudarte?",
            "behavior_notes": "Professional and friendly tone. Offer Corte, Barba, and Corte+Barba options.",
        },
        "business": {
            "display_name": "Mi Barbería",
            "contact_phone": "",
            "booking_notes": "Llegar 5 minutos antes. Cancelaciones con 2 horas de anticipación.",
        },
    },
)

PELUQUERIA_TEMPLATE = TenantTemplate(
    id="peluqueria-mixta",
    name="Peluquería Mixta",
    description="Mixed salon with unisex services, more price tiers, and two barbers.",
    services=[
        TemplateService(
            name="Corte Damas", code="OTHER", duration_minutes=45, price_cents=5000,
            description="Women's haircut",
        ),
        TemplateService(
            name="Corte Caballeros", code="C", duration_minutes=30, price_cents=3000,
            description="Men's haircut",
        ),
        TemplateService(
            name="Lavado y Secado", code="OTHER", duration_minutes=30, price_cents=2000,
            description="Wash & blow-dry",
        ),
        TemplateService(
            name="Tintura", code="OTHER", duration_minutes=90, price_cents=8000,
            description="Hair coloring (full head)",
        ),
        TemplateService(
            name="Corte Infantil", code="OTHER", duration_minutes=30, price_cents=2500,
            description="Children's haircut (under 12)",
        ),
    ],
    barbers=[
        TemplateBarber(name="Estilista 1"),
        TemplateBarber(name="Estilista 2"),
    ],
    schedules=[
        TemplateSchedule(barber_name="Estilista 1", weekday="mon", start_time="09:00", end_time="18:00"),
        TemplateSchedule(barber_name="Estilista 1", weekday="tue", start_time="09:00", end_time="18:00"),
        TemplateSchedule(barber_name="Estilista 1", weekday="wed", start_time="09:00", end_time="18:00"),
        TemplateSchedule(barber_name="Estilista 1", weekday="thu", start_time="09:00", end_time="18:00"),
        TemplateSchedule(barber_name="Estilista 1", weekday="fri", start_time="09:00", end_time="18:00"),
        TemplateSchedule(barber_name="Estilista 1", weekday="sat", start_time="09:00", end_time="13:00"),
        TemplateSchedule(barber_name="Estilista 2", weekday="mon", start_time="10:00", end_time="19:00"),
        TemplateSchedule(barber_name="Estilista 2", weekday="tue", start_time="10:00", end_time="19:00"),
        TemplateSchedule(barber_name="Estilista 2", weekday="wed", start_time="10:00", end_time="19:00"),
        TemplateSchedule(barber_name="Estilista 2", weekday="thu", start_time="10:00", end_time="19:00"),
        TemplateSchedule(barber_name="Estilista 2", weekday="fri", start_time="10:00", end_time="19:00"),
        TemplateSchedule(barber_name="Estilista 2", weekday="sat", start_time="10:00", end_time="13:00"),
    ],
    initial_settings={
        "bot": {
            "enabled": True,
            "greeting_text": "¡Hola! Soy el asistente virtual del salón. ¿Cómo podemos ayudarte hoy?",
            "behavior_notes": "Friendly and professional. Offer all services including tintura and wash.",
        },
        "business": {
            "display_name": "Mi Salón",
            "contact_phone": "",
            "booking_notes": "Por favor llegar 10 minutos antes. Cancelaciones con 24 horas de anticipación.",
        },
    },
)

_TEMPLATES: dict[str, TenantTemplate] = {
    BARBERIA_TEMPLATE.id: BARBERIA_TEMPLATE,
    PELUQUERIA_TEMPLATE.id: PELUQUERIA_TEMPLATE,
}


# ── Routes ────────────────────────────────────────────────────────────────


@router.get("", response_model=list[TenantTemplate])
def list_templates(
    _principal=Depends(get_current_principal),
) -> list[TenantTemplate]:
    """List all available onboarding templates."""
    return list(_TEMPLATES.values())


@router.get("/{template_id}", response_model=TenantTemplate)
def get_template(
    template_id: str,
    _principal=Depends(get_current_principal),
) -> TenantTemplate:
    """Get a specific onboarding template by ID."""
    template = _TEMPLATES.get(template_id)
    if template is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Template '{template_id}' not found",
        )
    return template


@router.post(
    "/{template_id}/apply/{tenant_id}",
    status_code=status.HTTP_200_OK,
)
def apply_template(
    template_id: str,
    tenant_id: UUID = Depends(tenant_id_from_path),
    session: Session = Depends(get_session),
    svc: SuperadminTenantService = Depends(get_superadmin_tenant_service),
    principal=Depends(get_current_principal),
) -> dict:
    """Apply an onboarding template to an existing tenant.

    This creates barbers, services, and schedules from the template,
    and sets the initial settings (only filling in missing values).
    Existing data is NOT overwritten — the template only adds what's
    missing.
    """
    template = _TEMPLATES.get(template_id)
    if template is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Template '{template_id}' not found",
        )

    tenant = svc.get(tenant_id)
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tenant {tenant_id} not found",
        )

    counts: dict = {"services_created": 0, "barbers_created": 0, "schedules_created": 0}

    # Import services (create if not exists by code).
    from sqlalchemy import select

    existing_services = set(
        session.execute(
            select(Service.code).where(Service.tenant_id == tenant_id)
        ).scalars().all()
    )
    for svc_data in template.services:
        if svc_data.code not in existing_services:
            from uuid import uuid4

            session.add(
                Service(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    name=svc_data.name,
                    code=svc_data.code,
                    duration_minutes=svc_data.duration_minutes,
                    price_cents=svc_data.price_cents,
                    description=svc_data.description,
                    is_active=True,
                )
            )
            counts["services_created"] += 1
            existing_services.add(svc_data.code)

    session.flush()

    # Import barbers + schedules.
    barber_name_to_id: dict[str, UUID] = {}
    existing_barber_names = set(
        session.execute(
            select(Barber.name).where(Barber.tenant_id == tenant_id)
        ).scalars().all()
    )
    for barber_data in template.barbers:
        if barber_data.name not in existing_barber_names:
            from uuid import uuid4

            bid = uuid4()
            session.add(
                Barber(
                    id=bid,
                    tenant_id=tenant_id,
                    name=barber_data.name,
                    is_active=True,
                )
            )
            barber_name_to_id[barber_data.name] = bid
            counts["barbers_created"] += 1
            existing_barber_names.add(barber_data.name)
        else:
            # Resolve existing barber.
            barber = session.execute(
                select(Barber).where(
                    Barber.tenant_id == tenant_id,
                    Barber.name == barber_data.name,
                )
            ).scalar_one_or_none()
            if barber:
                barber_name_to_id[barber_data.name] = barber.id

    session.flush()

    # Import schedules.
    for sched_data in template.schedules:
        barber_id = barber_name_to_id.get(sched_data.barber_name)
        if barber_id is None:
            continue
        # Check if schedule already exists for this barber+weekday.
        existing = session.execute(
            select(BarberSchedule).where(
                BarberSchedule.barber_id == barber_id,
                BarberSchedule.weekday == sched_data.weekday,
            )
        ).scalar_one_or_none()
        if existing is None:
            from datetime import time

            start_h, start_m = map(int, sched_data.start_time.split(":"))
            end_h, end_m = map(int, sched_data.end_time.split(":"))
            from uuid import uuid4

            session.add(
                BarberSchedule(
                    id=uuid4(),
                    barber_id=barber_id,
                    weekday=sched_data.weekday,
                    start_time=time(start_h, start_m),
                    end_time=time(end_h, end_m),
                )
            )
            counts["schedules_created"] += 1

    session.flush()

    # Fill in initial settings (only missing keys).
    if template.initial_settings:
        from packages.infrastructure.repositories import TenantRepository

        trepo = TenantRepository(session, tenant_id)
        existing = trepo.get_settings()
        config = dict(existing.config) if existing else {}
        for section, values in template.initial_settings.items():
            if section not in config:
                config[section] = {}
            if isinstance(config[section], dict) and isinstance(values, dict):
                for key, val in values.items():
                    config[section].setdefault(key, val)
            else:
                config.setdefault(section, values)
        trepo.upsert_settings(config)

    session.commit()

    # Audit log.
    audit = TenantAuditLogRepository(session, tenant_id)
    audit.log(
        event_type="template_applied",
        level="info",
        message=f"Onboarding template '{template.name}' applied",
        actor_scope="superadmin",
        actor_id=str(principal.user_id) if hasattr(principal, "user_id") else None,
        changed_fields=counts,
    )
    session.commit()

    return {
        "ok": True,
        "template": template_id,
        "tenant_id": str(tenant_id),
        "counts": counts,
    }

"""FastAPI application entry point.

Wires the database and registers every domain router. The JSON API
is the only HTTP surface of the backend now; the admin dashboard is
served by a separate Astro application under ``apps/admin-astro/``
that talks to the routes exposed here.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text

from apps.api.src.routes import (
    absences_router,
    daily_router,
    appointments_router,
    audit_logs_router,
    availability_router,
    barbers_router,
    barber_link_router,
    barber_workspace_router,
    bot_config_router,
    data_settings_router,
    draft_publish_router,
    export_import_router,
    extra_hours_router,
    feature_flags_router,
    health_router,
    identity_settings_router,
    import_sheets_router,
    ops_settings_router,
    overview_router,
    provider_configs_router,
    runtime_mode_router,
    schedules_router,
    services_router,
    superadmin_data_settings_router,
    superadmin_feature_flags_router,
    superadmin_health_router,
    superadmin_logs_router,
    superadmin_router,
    superadmin_tenant_users_router,
    templates_router,
    tenant_auth_router,
    tenant_sheets_router,
    tenant_users_router,
    tenants_router,
    webhooks_router,
)
from packages.infrastructure.db.session import get_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Warm optional infra and shut it down cleanly."""
    from packages.infrastructure.queue import get_queue
    from packages.infrastructure.redis import get_redis

    # Best-effort warm-up so production failures are visible at startup,
    # while local/dev can still degrade gracefully.
    get_redis().is_available()
    get_queue()
    try:
        yield
    finally:
        try:
            get_queue().shutdown(wait=True)
        except Exception:
            pass

app = FastAPI(
    title="Barber Agent API",
    version="0.6.0",
    description="Multi-tenant barber booking platform — greenfield rebuild.",
    lifespan=lifespan,
)

# --- JSON API routers -----------------------------------------------------

app.include_router(tenants_router)
app.include_router(barbers_router)
app.include_router(services_router)
app.include_router(schedules_router)
app.include_router(absences_router)
app.include_router(extra_hours_router)
app.include_router(availability_router)
app.include_router(appointments_router)
app.include_router(daily_router)
app.include_router(overview_router)

# Part 4 surface: operational settings self-service.
app.include_router(ops_settings_router)
# Bot + business config self-service.
app.include_router(bot_config_router)

# Part 3 surface.
app.include_router(superadmin_router)
app.include_router(provider_configs_router)
app.include_router(data_settings_router)
app.include_router(superadmin_data_settings_router)
app.include_router(webhooks_router)

# Tenant auth.
app.include_router(tenant_auth_router)

# Tenant Sheets self-service.
app.include_router(tenant_sheets_router)

# Import preview + apply.
app.include_router(import_sheets_router)

# Audit / operational logs.
app.include_router(audit_logs_router)
app.include_router(superadmin_logs_router)

# --- New: Phase 5 surface -------------------------------------------------

# Health / status center.
app.include_router(health_router)
app.include_router(superadmin_health_router)

# Feature flags.
app.include_router(feature_flags_router)
app.include_router(superadmin_feature_flags_router)

# Draft/publish workflow.
app.include_router(draft_publish_router)

# Export/import (backup/restore).
app.include_router(export_import_router)

# Onboarding templates.
app.include_router(templates_router)

# Tenant user management (RBAC).
app.include_router(tenant_users_router)
app.include_router(superadmin_tenant_users_router)

# Runtime mode introspection.
app.include_router(runtime_mode_router)

# Barber workspace.
app.include_router(barber_workspace_router)

# Identity settings.
app.include_router(identity_settings_router)

# Barber-user link.
app.include_router(barber_link_router)


# --- Meta ------------------------------------------------------------------


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    """Liveness + DB ping. Returns `ok` when the database is reachable."""

    # Use the same dependency the routes use, so a /health hit also
    # exercises the session lifecycle.
    for db in get_db():
        db.execute(text("SELECT 1"))
        break
    return {"status": "ok"}

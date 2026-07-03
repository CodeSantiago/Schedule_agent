"""Routes package: re-exports each router under a flat namespace."""

from apps.api.src.routes.absences import router as absences_router
from apps.api.src.routes.audit_logs import (
    superadmin_router as superadmin_logs_router,
    tenant_router as audit_logs_router,
)
from apps.api.src.routes.bot_config import router as bot_config_router
from apps.api.src.routes.data_settings import (
    superadmin_router as superadmin_data_settings_router,
    tenant_router as data_settings_router,
)
from apps.api.src.routes.draft_publish import router as draft_publish_router
from apps.api.src.routes.export_import import router as export_import_router
from apps.api.src.routes.feature_flags import (
    superadmin_router as superadmin_feature_flags_router,
    tenant_router as feature_flags_router,
)
from apps.api.src.routes.health import (
    superadmin_router as superadmin_health_router,
    tenant_router as health_router,
)
from apps.api.src.routes.ops_settings import router as ops_settings_router
from apps.api.src.routes.daily import router as daily_router
from apps.api.src.routes.appointments import router as appointments_router
from apps.api.src.routes.availability import router as availability_router
from apps.api.src.routes.barbers import router as barbers_router
from apps.api.src.routes.extra_hours import router as extra_hours_router
from apps.api.src.routes.overview import router as overview_router
from apps.api.src.routes.provider_configs import router as provider_configs_router
from apps.api.src.routes.schedules import router as schedules_router
from apps.api.src.routes.services import router as services_router
from apps.api.src.routes.superadmin import router as superadmin_router
from apps.api.src.routes.import_sheets import router as import_sheets_router
from apps.api.src.routes.templates import router as templates_router
from apps.api.src.routes.tenant_auth import router as tenant_auth_router
from apps.api.src.routes.tenant_sheets import router as tenant_sheets_router
from apps.api.src.routes.tenant_users import (
    superadmin_router as superadmin_tenant_users_router,
    tenant_router as tenant_users_router,
)
from apps.api.src.routes.tenants import router as tenants_router
from apps.api.src.routes.webhooks import router as webhooks_router
from apps.api.src.routes.runtime_mode import router as runtime_mode_router
from apps.api.src.routes.barber_workspace import router as barber_workspace_router
from apps.api.src.routes.identity_settings import router as identity_settings_router
from apps.api.src.routes.barber_link import router as barber_link_router

__all__ = [
    "absences_router",
    "daily_router",
    "appointments_router",
    "audit_logs_router",
    "availability_router",
    "barbers_router",
    "barber_link_router",
    "bot_config_router",
    "data_settings_router",
    "draft_publish_router",
    "export_import_router",
    "extra_hours_router",
    "feature_flags_router",
    "health_router",
    "identity_settings_router",
    "import_sheets_router",
    "ops_settings_router",
    "overview_router",
    "provider_configs_router",
    "schedules_router",
    "services_router",
    "superadmin_data_settings_router",
    "superadmin_feature_flags_router",
    "superadmin_health_router",
    "superadmin_logs_router",
    "superadmin_router",
    "superadmin_tenant_users_router",
    "templates_router",
    "tenant_auth_router",
    "tenant_sheets_router",
    "tenant_users_router",
    "tenants_router",
    "webhooks_router",
    "runtime_mode_router",
    "barber_workspace_router",
]

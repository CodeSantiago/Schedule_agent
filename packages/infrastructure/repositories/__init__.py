"""Tenant-scoped repositories for every tenant-aware table.

The base class (`base.TenantScopedRepository`) enforces that every public
method filters by `tenant_id`. Each concrete repo adds the few extra
queries the application layer needs (e.g. by-barber schedules, by-date
absences, by-date appointments).
"""

from packages.infrastructure.repositories.base import TenantScopedRepository
from packages.infrastructure.repositories.barbers import BarberRepository
from packages.infrastructure.repositories.messaging import (
    ConversationSessionRepository,
    IncomingMessageRepository,
    OutgoingMessageRepository,
)
from packages.infrastructure.repositories.providers import ProviderConfigRepository
from packages.infrastructure.repositories.schedules import (
    ScheduleRepository,
    AbsenceRepository,
    ExtraHourRepository,
)
from packages.infrastructure.repositories.appointments import (
    AppointmentRepository,
)
from packages.infrastructure.repositories.audit_log import TenantAuditLogRepository
from packages.infrastructure.repositories.services import ServiceRepository
from packages.infrastructure.repositories.tenants import TenantRepository

__all__ = [
    "AbsenceRepository",
    "AppointmentRepository",
    "BarberRepository",
    "ConversationSessionRepository",
    "ExtraHourRepository",
    "IncomingMessageRepository",
    "OutgoingMessageRepository",
    "ProviderConfigRepository",
    "ScheduleRepository",
    "ServiceRepository",
    "TenantAuditLogRepository",
    "TenantRepository",
    "TenantScopedRepository",
]

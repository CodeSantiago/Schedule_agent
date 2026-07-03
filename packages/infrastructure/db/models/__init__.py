"""SQLAlchemy ORM models for the multi-tenant barber platform.

All tenant-scoped tables include `tenant_id` and a composite index/unique
constraint where useful. Keep this module free of business logic.
"""

from packages.infrastructure.db.models.appointments import Appointment
from packages.infrastructure.db.models.auth import ApiToken, SuperadminUser
from packages.infrastructure.db.models.tenant_user import TenantUser
from packages.infrastructure.db.models.audit_log import TenantAuditLog
from packages.infrastructure.db.models.messaging import (
    ConversationLock,
    ConversationSession,
    IdempotencyKey,
    IncomingMessage,
    OutgoingMessage,
)
from packages.infrastructure.db.models.providers import ProviderConfig
from packages.infrastructure.db.models.scheduling import (
    Barber,
    BarberAbsence,
    BarberExtraHour,
    BarberSchedule,
    Service,
)
from packages.infrastructure.db.models.tenants import Tenant, TenantSetting

__all__ = [
    "ApiToken",
    "Appointment",
    "Barber",
    "BarberAbsence",
    "BarberExtraHour",
    "BarberSchedule",
    "ConversationLock",
    "ConversationSession",
    "IdempotencyKey",
    "IncomingMessage",
    "OutgoingMessage",
    "ProviderConfig",
    "Service",
    "SuperadminUser",
    "Tenant",
    "TenantAuditLog",
    "TenantSetting",
    "TenantUser",
]

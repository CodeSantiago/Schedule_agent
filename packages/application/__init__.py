"""Application layer for the tenant operational core.

Coordinates repositories with the pure domain layer:

- The HTTP layer hands a Pydantic request here.
- This layer loads the relevant ORM rows, maps them to domain shapes,
  calls the domain logic, and persists the result.
- Persistence is committed by the caller (the FastAPI route or the test
  fixture); the service itself only calls `session.flush()` to surface
  unique-constraint violations eagerly.

Adding new write paths (cancel appointment, reschedule, ...) belongs in
this package, not in the API layer. The domain layer stays pure.
"""

from packages.application.auth import AuthError, AuthService, Principal
from packages.application.intake import Intent, IntakeService, classify_intent
from packages.application.providers import (
    ProviderConfigService,
    UnknownProviderKindError,
)
from packages.application.scheduling.booking_service import BookingService
from packages.application.superadmin import SuperadminTenantService

__all__ = [
    "AuthError",
    "AuthService",
    "BookingService",
    "Intent",
    "IntakeService",
    "Principal",
    "ProviderConfigService",
    "SuperadminTenantService",
    "UnknownProviderKindError",
    "classify_intent",
]

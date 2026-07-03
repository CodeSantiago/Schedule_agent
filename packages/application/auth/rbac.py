"""RBAC (Role-Based Access Control) for tenant users.

Defines the role hierarchy and permission checks used by the API layer
to restrict sensitive operations. The role is stored on
``TenantUser.role`` and resolved at auth time from the ``Principal``.

Role hierarchy (high to low):
  ``owner`` > ``admin`` > ``staff`` > ``barber`` > ``viewer``

Permission reference (each role inherits all below it):

  **owner** — full access
  - manage_tenant_users (create/edit/delete users, change roles)
  - manage_bot_toggle (enable/disable bot)
  - manage_data_settings (source_of_truth, sync_mode)
  - manage_sheets_config (Sheets self-service)
  - manage_imports (preview/apply imports)
  - manage_destructive (delete barbers, services, bulk ops)
  - manage_feature_flags
  - manage_tenant_export_import
  - everything below

  **admin** — nearly everything except destructive/user management
  - manage_bot_config (greeting, behavior, business details)
  - manage_operations (closed dates, holiday ranges, barber overrides)
  - manage_appointments (create, cancel, reschedule, change status)
  - manage_barbers_services (CRUD on barbers, services, schedules)
  - read_everything

  **staff** — appointment operations + read
  - manage_appointments (create, cancel, reschedule, change status)
  - read_barbers_services
  - read_settings
  - read_dashboard

  **barber** — simple daily workspace + appointment operations
  - manage_appointments (change relevant appointment status from workspace)
  - read_barbers_services
  - read_settings
  - read_dashboard

  **viewer** — read-only
  - read_dashboard
  - read_appointments
  - read_barbers_services
  - read_settings
"""

from __future__ import annotations

from fastapi import HTTPException, status

from packages.application.auth import Principal

# Role ordering for comparison (higher index = more privileged).
_ROLE_ORDER: dict[str, int] = {
    "viewer": 0,
    "barber": 1,
    "staff": 2,
    "admin": 3,
    "owner": 4,
}

# Explicit permission map for clarity and audit.
_PERMISSIONS: dict[str, set[str]] = {
    "owner": {
        "manage_tenant_users",
        "manage_bot_toggle",
        "manage_data_settings",
        "manage_sheets_config",
        "manage_imports",
        "manage_destructive",
        "manage_feature_flags",
        "manage_tenant_export_import",
        "manage_bot_config",
        "manage_operations",
        "manage_appointments",
        "manage_barbers_services",
        "read_everything",
        "read_dashboard",
        "read_appointments",
        "read_barbers_services",
        "read_settings",
    },
    "admin": {
        "manage_tenant_users",
        "manage_bot_config",
        "manage_operations",
        "manage_appointments",
        "manage_barbers_services",
        "read_everything",
        "read_dashboard",
        "read_appointments",
        "read_barbers_services",
        "read_settings",
    },
    "staff": {
        "manage_appointments",
        "read_dashboard",
        "read_appointments",
        "read_barbers_services",
        "read_settings",
    },
    "barber": {
        "manage_appointments",
        "read_dashboard",
        "read_appointments",
        "read_barbers_services",
        "read_settings",
    },
    "viewer": {
        "read_dashboard",
        "read_appointments",
        "read_barbers_services",
        "read_settings",
    },
}

VALID_ROLES = frozenset(_ROLE_ORDER.keys())
ROLE_HIERARCHY = list(_ROLE_ORDER.keys())  # lowest → highest


class PermissionDeniedError(HTTPException):
    """Raised when a principal lacks the required permission.

    Returns 403 Forbidden with a generic message so callers cannot
    probe which specific permission is missing.
    """

    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="insufficient permissions",
        )


def role_has(role: str, permission: str) -> bool:
    """Check whether *any* principal with *role* has *permission*."""
    perms = _PERMISSIONS.get(role)
    if perms is None:
        return False
    return permission in perms


def check_permission(principal: Principal, permission: str) -> bool:
    """Check whether the principal has the given permission.

    Superadmin principals bypass all permission checks. Tenant
    principals are checked against their role.

    This is a function, not a FastAPI dependency, to avoid circular
    imports with ``deps.py``. Call it directly in route handlers::

        if not check_permission(principal, "manage_bot_toggle"):
            raise PermissionDeniedError()
    """
    if principal.scope == "superadmin":
        return True
    if principal.scope != "tenant":
        return False
    return role_has(principal.role, permission)


def get_principal_role(principal: Principal) -> str | None:
    """Return the principal's role, or None if not applicable."""
    if principal.scope != "tenant":
        return None
    return getattr(principal, "role", "admin")  # safe fallback


def get_role_level(role: str) -> int:
    """Return the numeric level of a role (higher = more privileged).

    Returns -1 for unknown roles.
    """
    return _ROLE_ORDER.get(role, -1)


def is_at_least(principal: Principal, min_role: str) -> bool:
    """Check whether the principal's role is at least *min_role*.

    Superadmin always passes. Unknown principal roles fail safe (deny).
    """
    if principal.scope == "superadmin":
        return True
    if principal.scope != "tenant":
        return False
    return get_role_level(principal.role) >= get_role_level(min_role)

"""Auth application service: superadmin + tenant user accounts + bearer tokens.

This is the single place that mutates `superadmin_users`,
`tenant_users`, and `api_tokens`. Routes never touch those tables
directly.

The service exposes:

- `create_superadmin`      — create a new platform owner (idempotent).
- `create_tenant_user`     — create a new per-tenant user account.
- `authenticate`           — verify superadmin email+password.
- `authenticate_tenant`    — verify tenant user email+password.
- `verify_bearer`          — turn a raw bearer token into a `Principal`
  for any scope.
- `revoke`                 — revoke a token.

The token table uses a polymorphic FK pattern: `superadmin_id` for
superadmin tokens, `tenant_user_id` + `tenant_id` for tenant tokens.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.domain.auth import IssuedToken, PasswordHasher, TokenIssuer
from packages.infrastructure.db.models.auth import (
    API_TOKEN_SCOPE_SUPERADMIN,
    API_TOKEN_SCOPE_TENANT,
    ApiToken,
    SuperadminUser,
)
from packages.infrastructure.db.models.tenant_user import TenantUser


class AuthError(Exception):
    """Raised on any authentication failure.

    The HTTP layer maps this to `401 Unauthorized`. We use one error
    type for all cases (unknown email, wrong password, revoked token,
    missing token) so callers do not leak which case they hit.
    """


@dataclass(frozen=True)
class Principal:
    """The authenticated caller.

    `scope` is `"superadmin"` or `"tenant"`. `user_id` is the
    `superadmin_users.id` (for superadmin) or `tenant_users.id` (for
    tenant). `tenant_id` is set only for tenant-scoped principals.
    `role` is set only for tenant principals (owner/admin/staff/barber/viewer).
    """

    user_id: UUID
    email: str
    scope: str
    tenant_id: UUID | None = None
    role: str | None = None


class AuthService:
    """Auth application service. Stateless; takes a `Session` per call."""

    def __init__(
        self,
        session: Session,
        *,
        hasher: Optional[PasswordHasher] = None,
        issuer: Optional[TokenIssuer] = None,
    ) -> None:
        self._session = session
        self._hasher = hasher or PasswordHasher()
        self._issuer = issuer or TokenIssuer()

    # --- Bootstrap ---------------------------------------------------------

    def create_superadmin(
        self, email: str, password: str, *, activate: bool = True
    ) -> SuperadminUser:
        """Create a superadmin (or return the existing one for that email).

        Intended for the CLI bootstrap path, not the HTTP API.
        """
        normalised = email.strip().lower()
        existing = self._session.execute(
            select(SuperadminUser).where(SuperadminUser.email == normalised)
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        user = SuperadminUser(
            id=uuid.uuid4(),
            email=normalised,
            password_hash=self._hasher.hash(password),
            is_active="true" if activate else "false",
        )
        self._session.add(user)
        self._session.flush()
        return user

    def create_tenant_user(
        self,
        tenant_id: UUID,
        email: str,
        password: str,
        name: str,
        *,
        activate: bool = True,
    ) -> TenantUser:
        """Create a tenant user (or return the existing one for that email).

        Email is globally unique across all tenants. Returns the existing
        row when the email is already registered (idempotent).
        """
        normalised = email.strip().lower()
        existing = self._session.execute(
            select(TenantUser).where(TenantUser.email == normalised)
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        user = TenantUser(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            email=normalised,
            password_hash=self._hasher.hash(password),
            name=name.strip(),
            is_active="true" if activate else "false",
        )
        self._session.add(user)
        self._session.flush()
        return user

    # --- Login -------------------------------------------------------------

    def authenticate(self, email: str, password: str, label: str = "") -> IssuedToken:
        """Verify `email`+`password` and mint a fresh bearer token.

        On any failure raises `AuthError`. The label is a free-form hint
        (e.g. `"cli"` or `"dashboard-2026-06-25"`) recorded on the token
        row so an operator can revoke it by name later.
        """
        normalised = email.strip().lower()
        user = self._session.execute(
            select(SuperadminUser).where(SuperadminUser.email == normalised)
        ).scalar_one_or_none()

        # Constant-time guard: always run a hash comparison so the
        # unknown-email and wrong-password paths take about the same
        # amount of time. We compare against a pre-computed sentinel
        # hash that is known to NOT match the supplied password.
        sentinel_hash = self._sentinel_hash()
        target_hash = user.password_hash if user is not None else sentinel_hash
        password_ok = self._hasher.verify(password, target_hash)

        if user is None or user.is_active != "true" or not password_ok:
            raise AuthError("invalid credentials")

        issued = self._issuer.issue()
        token_row = ApiToken(
            id=uuid.uuid4(),
            superadmin_id=user.id,
            token_hash=issued.hash,
            label=label or "",
            scope=API_TOKEN_SCOPE_SUPERADMIN,
        )
        self._session.add(token_row)
        self._session.flush()
        return issued

    def _sentinel_hash(self) -> str:
        """Return a hash that does not match any real password.

        We cache it on the hasher instance the first time we need it so
        the PBKDF2 cost (~150ms) is paid once per AuthService lifetime.
        """
        cached = getattr(self._hasher, "_sentinel_cache", None)
        if cached is not None:
            return cached
        # PBKDF2 of an empty-string-like secret + a fixed salt. Any real
        # password attempt will compute a different hash and `verify`
        # will return False, so the timing is roughly equivalent to the
        # wrong-password case.
        sentinel = self._hasher.hash("__barber_agent_unknown_user__")
        try:
            self._hasher._sentinel_cache = sentinel  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover - defensive
            pass
        return sentinel

    # --- Tenant login -------------------------------------------------------

    def authenticate_tenant(
        self, email: str, password: str, label: str = ""
    ) -> IssuedToken:
        """Verify tenant `email`+`password` and mint a tenant-scoped token.

        On any failure raises `AuthError`. The token is bound to the
        tenant user and carries the denormalised `tenant_id` for fast
        lookup.
        """
        normalised = email.strip().lower()
        user = self._session.execute(
            select(TenantUser).where(TenantUser.email == normalised)
        ).scalar_one_or_none()

        sentinel_hash = self._sentinel_hash()
        target_hash = user.password_hash if user is not None else sentinel_hash
        password_ok = self._hasher.verify(password, target_hash)

        if user is None or user.is_active != "true" or not password_ok:
            raise AuthError("invalid credentials")

        issued = self._issuer.issue()
        token_row = ApiToken(
            id=uuid.uuid4(),
            tenant_user_id=user.id,
            tenant_id=user.tenant_id,
            token_hash=issued.hash,
            label=label or "",
            scope=API_TOKEN_SCOPE_TENANT,
        )
        self._session.add(token_row)
        self._session.flush()
        return issued

    # --- Bearer lookup -----------------------------------------------------

    def verify_bearer(self, raw_token: str) -> Principal:
        """Turn a raw bearer token into a `Principal`.

        Raises `AuthError` for missing/unknown/revoked tokens.
        """
        if not raw_token:
            raise AuthError("missing bearer token")
        token_hash = self._issuer.hash_raw(raw_token)
        row = self._session.execute(
            select(ApiToken).where(ApiToken.token_hash == token_hash)
        ).scalar_one_or_none()
        if row is None or row.revoked_at is not None:
            raise AuthError("invalid bearer token")

        # Resolve the principal user based on scope.
        if row.scope == API_TOKEN_SCOPE_TENANT:
            user = self._session.execute(
                select(TenantUser).where(TenantUser.id == row.tenant_user_id)
            ).scalar_one_or_none()
            if user is None or user.is_active != "true":
                raise AuthError("invalid bearer token")
            principal = Principal(
                user_id=user.id,
                email=user.email,
                scope=row.scope,
                tenant_id=row.tenant_id,
                role=getattr(user, "role", "admin"),
            )
        elif row.scope == API_TOKEN_SCOPE_SUPERADMIN:
            user = self._session.execute(
                select(SuperadminUser).where(SuperadminUser.id == row.superadmin_id)
            ).scalar_one_or_none()
            if user is None or user.is_active != "true":
                raise AuthError("invalid bearer token")
            principal = Principal(
                user_id=user.id, email=user.email, scope=row.scope
            )
        else:
            raise AuthError("invalid bearer token")

        # Best-effort `last_used_at`; never let a logging failure mask
        # the authentication result.
        try:
            row.last_used_at = datetime.now(timezone.utc).isoformat()
            self._session.flush()
        except Exception:  # pragma: no cover - defensive
            self._session.rollback()

        return principal

    # --- Lifecycle (for tests / admin) -------------------------------------

    def revoke(self, raw_token: str) -> bool:
        """Revoke the token matching `raw_token`. Returns True if a row was
        updated (i.e. it existed and was active)."""
        if not raw_token:
            return False
        token_hash = self._issuer.hash_raw(raw_token)
        row = self._session.execute(
            select(ApiToken).where(ApiToken.token_hash == token_hash)
        ).scalar_one_or_none()
        if row is None or row.revoked_at is not None:
            return False
        row.revoked_at = datetime.now(timezone.utc).isoformat()
        self._session.flush()
        return True

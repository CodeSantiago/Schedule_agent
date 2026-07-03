"""Provider-config application service.

Thin orchestration layer for the per-tenant provider wiring endpoints.
The CRUD goes through `ProviderConfigRepository`; this service is
responsible for input validation and for the "at most one active
config per kind" invariant the repo also enforces.

Sub-modules
-----------
- ``kapso_config`` — Kapso-specific config normalization helpers (global key,
  per-tenant settings, transport-readable shape).
"""

from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from packages.application.providers.kapso_config import (  # noqa: F401
    KAPSO_CREDENTIAL_KEYS,
    KAPSO_SETTINGS_KEYS,
    compute_kapso_credentials,
    compute_kapso_settings,
    get_kapso_platform_config,
    read_kapso_transport_config,
    resolve_api_key,
    validate_kapso_config,
    verify_webhook_signature,
)
from packages.infrastructure.db.models.providers import PROVIDER_KIND_VALUES, ProviderConfig
from packages.infrastructure.repositories.providers import ProviderConfigRepository


class UnknownProviderKindError(ValueError):
    """The requested `kind` is not in the `PROVIDER_KIND_VALUES` enum."""


class ProviderConfigService:
    def __init__(self, session: Session, tenant_id: UUID) -> None:
        self._session = session
        self._tenant_id = tenant_id
        self._repo = ProviderConfigRepository(session, tenant_id)

    @property
    def session(self) -> Session:
        """Expose the underlying session so the route can commit."""
        return self._session

    @property
    def repo(self) -> ProviderConfigRepository:
        return self._repo

    def list_for_kind(self, kind: str) -> list[ProviderConfig]:
        self._validate_kind(kind)
        return self._repo.list_for_kind(kind)

    def get_active_for_kind(self, kind: str) -> ProviderConfig | None:
        self._validate_kind(kind)
        return self._repo.get_active_for_kind(kind)

    def get(self, config_id: UUID) -> ProviderConfig | None:
        return self._repo.get_by_id(config_id)

    def create(
        self,
        *,
        kind: str,
        label: str,
        provider_name: str,
        credentials: Optional[dict] = None,
        settings: Optional[dict] = None,
        is_active: bool = True,
    ) -> ProviderConfig:
        self._validate_kind(kind)
        if not label or not label.strip():
            raise ValueError("label must not be empty")
        if not provider_name or not provider_name.strip():
            raise ValueError("provider_name must not be empty")
        row = ProviderConfig(
            kind=kind,
            label=label.strip(),
            provider_name=provider_name.strip(),
            credentials=credentials or {},
            settings=settings or {},
            is_active=is_active,
        )
        added = self._repo.add(row)
        self._session.flush()
        return added

    def update(
        self,
        config_id: UUID,
        *,
        label: Optional[str] = None,
        provider_name: Optional[str] = None,
        credentials: Optional[dict] = None,
        settings: Optional[dict] = None,
        is_active: Optional[bool] = None,
    ) -> ProviderConfig | None:
        row = self._repo.get_by_id(config_id)
        if row is None:
            return None
        if label is not None:
            if not label.strip():
                raise ValueError("label must not be empty")
            row.label = label.strip()
        if provider_name is not None:
            if not provider_name.strip():
                raise ValueError("provider_name must not be empty")
            row.provider_name = provider_name.strip()
        if credentials is not None:
            row.credentials = credentials
        if settings is not None:
            row.settings = settings
        # When flipping the target ON, the partial unique index
        # `uq_provider_active_per_kind` must never see two active rows
        # in the same statement batch. Deactivate the sibling, flush,
        # then activate the target — mirroring the repo's `set_active`
        # order so the flush never trips the index.
        if is_active is True:
            active = self._repo.get_active_for_kind(row.kind)
            if active is not None and active.id != row.id:
                active.is_active = False
                self._session.flush()
            row.is_active = True
        elif is_active is False:
            row.is_active = False
        self._session.flush()
        return row

    def activate(self, config_id: UUID) -> ProviderConfig | None:
        return self._repo.set_active(config_id)

    def deactivate(self, config_id: UUID) -> bool:
        return self._repo.deactivate(config_id)

    def delete(self, config_id: UUID) -> bool:
        return self._repo.delete(config_id)

    @staticmethod
    def _validate_kind(kind: str) -> None:
        if kind not in PROVIDER_KIND_VALUES:
            raise UnknownProviderKindError(
                f"unknown provider kind {kind!r}; "
                f"valid kinds: {sorted(PROVIDER_KIND_VALUES)}"
            )

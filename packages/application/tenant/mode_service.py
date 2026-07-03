"""Tenant operating mode service.

Resolves the effective data source for each domain at runtime based on
the tenant's ``source_of_truth`` setting (database | google_sheets | hybrid).

The service is the central authority for "what mode is this tenant in?"
and provides read-through helpers that consult Google Sheets when the
tenant runs in sheets-driven mode.

Also manages **appointment write-back** via GoogleSheetsWriter and
**customer identity mode** (how ``customer_name`` is assembled from
structured fields).
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.orm import Session

from packages.application.providers.sheets_reader import (
    GoogleSheetsReader,
    SheetsNotConfigured,
    SheetsReadError,
)
from packages.application.providers.sheets_writer import (
    GoogleSheetsWriter,
)
from packages.infrastructure.db.models.providers import ProviderConfig
from packages.infrastructure.db.models.tenants import TenantSetting
from packages.infrastructure.repositories import ProviderConfigRepository

logger = logging.getLogger(__name__)

SOURCE_DATABASE = "database"
SOURCE_GOOGLE_SHEETS = "google_sheets"
SOURCE_HYBRID = "hybrid"
VALID_SOURCES = {SOURCE_DATABASE, SOURCE_GOOGLE_SHEETS, SOURCE_HYBRID}

# Default operations config key
DATA_DEFAULTS = {"source_of_truth": SOURCE_DATABASE, "sync_mode": "manual"}

# Valid customer identity modes
IDENTITY_MODES = frozenset({
    "full_name",           # single customer_name field (default)
    "first_name_last_name", # first_name + last_name concatenated
    "dni",                 # only DNI/national ID
    "full_name_dni",       # full name + DNI
    "first_name_last_name_dni",  # structured first+last name + DNI
})


class TenantModeError(Exception):
    """Raised when mode resolution fails."""


class TenantModeService:
    """Resolve the effective operating mode for a tenant.

    Reads the tenant's ``data.source_of_truth`` setting and provides
    helpers that consult Google Sheets when the tenant is in
    sheets-driven or hybrid mode.

    Also manages appointment write-back and customer identity formatting.

    Usage::

        svc = TenantModeService(session, tenant_id)
        mode = svc.get_mode()  # "database" | "google_sheets" | "hybrid"
        is_active = svc.is_barber_active(barber_id, weekday)
        bot_enabled = svc.get_bot_enabled()
        writer = svc.get_writer()  # GoogleSheetsWriter | None
        name = svc.format_customer_name("Juan", "Pérez", "12345678")
    """

    def __init__(self, session: Session, tenant_id: UUID) -> None:
        self._session = session
        self._tenant_id = tenant_id
        self._provider_repo = ProviderConfigRepository(session, tenant_id)
        self._reader: GoogleSheetsReader | None = None
        self._writer: GoogleSheetsWriter | None = None

    # ── Public API ──────────────────────────────────────────────────────

    def get_mode(self) -> str:
        """Return the tenant's configured ``source_of_truth``.

        Never returns ``None`` — missing settings default to ``"database"``.
        """
        return self._read_source_of_truth()

    def is_database_mode(self) -> bool:
        return self.get_mode() == SOURCE_DATABASE

    def is_sheets_mode(self) -> bool:
        return self.get_mode() == SOURCE_GOOGLE_SHEETS

    def is_hybrid_mode(self) -> bool:
        return self.get_mode() == SOURCE_HYBRID

    def effective_source(self, domain: str = "general") -> str:
        """Return the effective source for a given domain.

        In hybrid mode, different domains may be served by different
        sources. Currently:
        - ``bot`` → sheets (for bot_enabled, barber_active)
        - ``scheduling`` → database
        - ``appointments`` → database
        - ``general`` → database

        This is a future extension point. Currently hybrid defers to
        sheets for operational bot state and DB for everything else.
        """
        mode = self.get_mode()
        if mode == SOURCE_DATABASE:
            return SOURCE_DATABASE
        if mode == SOURCE_GOOGLE_SHEETS:
            return SOURCE_GOOGLE_SHEETS
        # Hybrid: domain-specific routing
        if domain in ("bot", "barber_status"):
            return SOURCE_GOOGLE_SHEETS
        return SOURCE_DATABASE

    def get_bot_enabled(self) -> bool:
        """Return whether the bot is enabled.

        In database mode, reads from settings. In sheets/hybrid mode,
        reads from the Google Sheets CONFIG tab.
        """
        mode = self.get_mode()
        if mode == SOURCE_DATABASE:
            return self._read_db_bot_enabled()

        # Sheets or hybrid: consult the sheet
        reader = self._get_reader()
        if reader is None:
            return self._read_db_bot_enabled()
        try:
            return reader.fetch_bot_status()
        except SheetsReadError:
            logger.warning("[mode] sheets read failed, falling back to DB")
            return self._read_db_bot_enabled()

    def is_barber_active(self, barber_code: str, weekday: str) -> bool:
        """Check if a barber is active on a given weekday.

        In database mode, always returns True (active status is managed
        per-barber row). In sheets/hybrid mode, consults the CONFIG tab
        for daily status.
        """
        mode = self.get_mode()
        if mode == SOURCE_DATABASE:
            return True  # DB barbers are always "active" by default

        reader = self._get_reader()
        if reader is None:
            return True
        try:
            return reader.is_barber_active_on_day(barber_code, weekday)
        except SheetsReadError:
            logger.warning("[mode] sheets barber status read failed, default active")
            return True

    def get_sheets_reader(self) -> GoogleSheetsReader | None:
        """Return the reader instance (or None if no sheets config exists)."""
        return self._get_reader()

    def get_sheets_writer(self) -> GoogleSheetsWriter | None:
        """Return the writer instance (or None if no write credential)."""
        return self._get_writer()

    def get_identity_mode(self) -> str:
        """Return the configured customer identity mode.

        Reads ``booking.customer_identity_mode`` from tenant settings.
        Defaults to ``"full_name"`` when not set.
        """
        return self._read_config_value(
            ["booking", "customer_identity_mode"], "full_name"
        )

    def format_customer_name(
        self,
        customer_name: str | None,
        customer_last_name: str | None,
        customer_dni: str | None,
    ) -> str:
        """Format the display ``customer_name`` based on tenant identity mode.

        Args:
            customer_name: The raw name/display value (or first name).
            customer_last_name: Optional last name.
            customer_dni: Optional DNI/national ID.

        Returns:
            The formatted display string.
        """
        mode = self.get_identity_mode()

        if mode == "dni":
            return customer_dni or customer_name or "—"
        if mode == "first_name_last_name":
            parts = [p for p in [customer_name, customer_last_name] if p]
            return " ".join(parts) if parts else "—"
        if mode == "full_name_dni":
            base = customer_name or "—"
            if customer_dni:
                return f"{base} ({customer_dni})"
            return base
        if mode == "first_name_last_name_dni":
            parts = [p for p in [customer_name, customer_last_name] if p]
            base = " ".join(parts) if parts else "—"
            if customer_dni:
                return f"{base} ({customer_dni})"
            return base
        # full_name (default)
        return customer_name or "—"

    def get_runtime_summary(self) -> dict:
        """Return a summary dict for the runtime mode endpoint."""
        mode = self.get_mode()
        reader = self._get_reader()
        writer = self._get_writer()
        sheets_connected = reader is not None

        sheets_state: dict | None = None
        if reader is not None:
            try:
                sheets_state = reader.fetch_operational_state()
            except SheetsReadError:
                sheets_state = {"error": "could not read sheets"}

        identity_mode = self.get_identity_mode()
        sheets_write_back = writer is not None and writer.is_writeable

        return {
            "mode": mode,
            "sheets_connected": sheets_connected,
            "sheets_state": sheets_state,
            "identity_mode": identity_mode,
            "domains": {
                "bot": self.effective_source("bot"),
                "barber_status": self.effective_source("barber_status"),
                "scheduling": self.effective_source("scheduling"),
                "appointments": self.effective_source("appointments"),
                "general": self.effective_source("general"),
            },
            "constraints": {
                "sheets_write_back": sheets_write_back,
                "write_back_type": "access_token" if sheets_write_back else "none",
                "note": (
                    "Sheets write-back is enabled when an OAuth/service-account "
                    "access_token is configured in the sheets provider credentials."
                    if sheets_write_back
                    else (
                        "Sheets mode is read-only. Configure an access_token in the "
                        "sheets provider credentials to enable appointment write-back."
                    )
                ),
            },
        }

    # ── Internal helpers ────────────────────────────────────────────────

    def _read_source_of_truth(self) -> str:
        """Read source_of_truth from tenant_settings."""
        settings = (
            self._session.query(TenantSetting)
            .filter(TenantSetting.tenant_id == self._tenant_id)
            .first()
        )
        if settings is None:
            return DATA_DEFAULTS["source_of_truth"]
        config = dict(settings.config or {})
        data = config.get("data", {})
        if not isinstance(data, dict):
            return DATA_DEFAULTS["source_of_truth"]
        return data.get("source_of_truth", DATA_DEFAULTS["source_of_truth"])

    def _read_db_bot_enabled(self) -> bool:
        """Read bot enabled from tenant operational settings."""
        settings = (
            self._session.query(TenantSetting)
            .filter(TenantSetting.tenant_id == self._tenant_id)
            .first()
        )
        if settings is None:
            return True
        config = dict(settings.config or {})
        bot = config.get("bot", {})
        if not isinstance(bot, dict):
            return True
        return bot.get("enabled", True)

    def _get_reader(self) -> GoogleSheetsReader | None:
        """Lazy-build the sheets reader from active provider config."""
        if self._reader is not None:
            return self._reader

        active = self._provider_repo.get_active_for_kind("sheets")
        if active is None:
            return None

        sid = (active.settings or {}).get("spreadsheet_id", "")
        if not sid:
            logger.warning("[mode] sheets config exists but no spreadsheet_id")
            return None

        api_key = (active.credentials or {}).get("api_key")
        sheet_name = (active.settings or {}).get("sheet_name", "CONFIG")
        range_ = (active.settings or {}).get("range", "A1:G30")

        self._reader = GoogleSheetsReader(
            spreadsheet_id=sid,
            api_key=api_key,
            config_tab=sheet_name,
            config_range=range_,
        )
        return self._reader

    def _get_writer(self) -> GoogleSheetsWriter | None:
        """Lazy-build the sheets writer from active provider config.

        The writer needs an OAuth/service-account ``access_token`` in the
        provider credentials. If only an ``api_key`` is available, the
        writer will be created but non-writeable (``is_writeable`` = False).
        This is the honest first pass: the pipeline is wired, the credential
        gap is explicit.
        """
        if self._writer is not None:
            return self._writer

        active = self._provider_repo.get_active_for_kind("sheets")
        if active is None:
            return None

        sid = (active.settings or {}).get("spreadsheet_id", "")
        if not sid:
            logger.warning("[mode] sheets config exists but no spreadsheet_id")
            return None

        # Write-back requires an access_token (OAuth or service-account JWT).
        # API key alone cannot write — but we still build the writer so the
        # caller can check ``is_writeable``.
        access_token = (active.credentials or {}).get("access_token")
        sheet_tab = (active.settings or {}).get("appointments_tab", "APPOINTMENTS")

        self._writer = GoogleSheetsWriter(
            spreadsheet_id=sid,
            access_token=access_token,
            sheet_tab=sheet_tab,
        )
        return self._writer

    def _read_config_value(
        self, keys: list[str], default: str = ""
    ) -> str:
        """Read a nested config value from tenant_settings.

        ``keys`` is a path into the config dict, e.g.
        ``["booking", "customer_identity_mode"]``.
        """
        settings = (
            self._session.query(TenantSetting)
            .filter(TenantSetting.tenant_id == self._tenant_id)
            .first()
        )
        if settings is None:
            return default
        config = dict(settings.config or {})
        val: object = config
        for key in keys:
            if not isinstance(val, dict):
                return default
            val = val.get(key)
            if val is None:
                return default
        if isinstance(val, str):
            return val
        return default

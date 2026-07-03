"""Google Sheets runtime reader — HTTP/API access for operational state.

Reads operational bot data from a Google Sheet using the public Sheets
API (no gspread dependency, no service-account auth required). Access is
read-only and works with:

- **Public sheets** — no API key needed (sheet must be shared publicly).
- **API-key access** — provide a Google Sheets API key in the tenant's
  sheets provider config.

The reader mirrors the original solo-tenant production workflow where a
CONFIG tab holds bot-enabled status and per-barber daily availability.

Constraints (honest)
--------------------
- **Read-only**. No write-back support. Tenant settings must be updated
  via the admin dashboard.
- **Public sheet or API key only**. No OAuth / service-account path.
- **Simple key-value CONFIG tab**. The reader expects a layout similar
  to the legacy production sheet:
    Row 3: BOT | activo | ...
    Row 5+: BARBERO | LUNES | MARTES | ... | VIERNES
- **No service/barber catalog reading**. Only operational state.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ── Constants matching the legacy production workflow ───────────────────

WEEKDAYS = ("LUNES", "MARTES", "MIERCOLES", "JUEVES", "VIERNES")
BOT_KEYWORD = "BOT"
BARBERO_HEADER = "BARBERO"
ACTIVE_VALUES = frozenset({"activo", "active", "on", "true", "si", "yes"})
INACTIVE_VALUES = frozenset({"ausente", "inactivo", "apagado", "off", "false", "no"})

# Default CONFIG sheet range — reads enough to cover bot status + 20 barbers across 7 cols
DEFAULT_CONFIG_RANGE = "A1:G30"

# Default SERVICE tab and range
DEFAULT_SERVICE_TAB = "SERVICIOS"
DEFAULT_SERVICE_RANGE = "A1:E50"  # Code | Name | Duration | Price | Active


# ── Errors ──────────────────────────────────────────────────────────────


class SheetsReadError(Exception):
    """Raised when the Sheets API call fails or returns unexpected data."""


class SheetsNotConfigured(SheetsReadError):
    """Raised when no sheets provider config exists for the tenant."""


# ── Main reader ─────────────────────────────────────────────────────────


class GoogleSheetsReader:
    """Read operational state from a Google Sheet's CONFIG tab.

    Usage::

        reader = GoogleSheetsReader(spreadsheet_id, api_key="…")
        status = reader.fetch_bot_status()
        barbers = reader.fetch_weekly_status()
        print(reader.check_connection())
    """

    BASE_URL = "https://sheets.googleapis.com/v4/spreadsheets/{sid}/values/{range}"

    def __init__(
        self,
        spreadsheet_id: str,
        api_key: str | None = None,
        config_tab: str = "CONFIG",
        config_range: str = DEFAULT_CONFIG_RANGE,
        service_tab: str = DEFAULT_SERVICE_TAB,
        service_range: str = DEFAULT_SERVICE_RANGE,
        timeout_s: int = 10,
    ) -> None:
        self._spreadsheet_id = spreadsheet_id
        self._api_key = api_key
        self._config_tab = config_tab
        self._config_range = config_range
        self._service_tab = service_tab
        self._service_range = service_range
        self._timeout = timeout_s
        self._client = httpx.Client(timeout=httpx.Timeout(timeout_s))

    # ── Public API ──────────────────────────────────────────────────────

    def check_connection(self) -> dict[str, Any]:
        """Verify the sheet is reachable.

        Returns a dict with ``ok`` (bool) and either ``error`` or details.
        This call does NOT require a specific tab layout — it just fetches
        a single cell to confirm access.
        """
        try:
            raw_range = f"{self._config_tab}!A1:A1"
            url = self.BASE_URL.format(
                sid=self._spreadsheet_id, range=raw_range
            )
            params = self._build_params()
            resp = self._client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            return {
                "ok": True,
                "sheet_id": self._spreadsheet_id,
                "tab": self._config_tab,
                "has_api_key": bool(self._api_key),
            }
        except httpx.HTTPStatusError as exc:
            return {
                "ok": False,
                "error": f"Sheets API returned {exc.response.status_code}: {exc.response.text[:200]}",
            }
        except httpx.RequestError as exc:
            return {"ok": False, "error": f"Request failed: {exc}"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def fetch_bot_status(self) -> bool:
        """Read whether the bot is enabled from the CONFIG tab.

        Looks at row 3 (0-indexed row 2) where column A = "BOT" and
        column B holds the status ("activo" / "apagado").

        Returns ``True`` (enabled) on any read failure so the bot
        remains operative rather than breaking.
        """
        try:
            rows = self._fetch_config_rows()
            for row in rows:
                if len(row) >= 2 and row[0].strip().upper() == BOT_KEYWORD:
                    val = row[1].strip().lower()
                    is_active = val in ACTIVE_VALUES
                    logger.info(
                        "[sheets_reader] bot status: raw=%r → active=%s",
                        val, is_active,
                    )
                    return is_active
            logger.warning("[sheets_reader] BOT row not found in CONFIG tab")
            return True  # safe default
        except SheetsReadError:
            logger.exception("[sheets_reader] failed to read bot status")
            return True  # safe default

    def fetch_barber_weekly_status(self) -> dict[str, dict[str, bool]]:
        """Read per-barber weekly availability from the CONFIG tab.

        Returns a dict keyed by barber short code (e.g. "O", "R", "A"),
        each value being ``{ "LUNES": True, "MARTES": False, ... }``
        where ``True`` means active/available.

        The header row is identified by ``BARBERO`` in column A.
        Data rows follow with barber code in column A and weekday
        status in columns B-F.
        """
        try:
            rows = self._fetch_config_rows()
        except SheetsReadError:
            logger.exception("[sheets_reader] failed to fetch barber status")
            return {}

        # Find the header row (contains BARBERO in col A)
        data_start = None
        for i, row in enumerate(rows):
            if row and row[0].strip().upper() == BARBERO_HEADER:
                data_start = i + 1
                break

        if data_start is None or data_start >= len(rows):
            logger.warning("[sheets_reader] no barber data rows found")
            return {}

        result: dict[str, dict[str, bool]] = {}
        for row in rows[data_start:]:
            if not row or not row[0].strip():
                continue
            code = row[0].strip().upper()
            # Extract first letter or short code (e.g. "O (Omar)" → "O")
            short_code = code[0] if code else ""
            if not short_code:
                continue

            day_status: dict[str, bool] = {}
            for day_idx, day_name in enumerate(WEEKDAYS):
                if day_idx + 1 < len(row):
                    val = row[day_idx + 1].strip().lower()
                    day_status[day_name] = val in ACTIVE_VALUES
                else:
                    day_status[day_name] = True  # default active
            result[short_code] = day_status

        logger.info(
            "[sheets_reader] barber weekly status: %d barbers read",
            len(result),
        )
        return result

    def is_barber_active_on_day(self, barber_code: str, weekday: str) -> bool:
        """Convenience: check a single barber on a single day.

        ``barber_code`` is the short code (e.g. "O", "R", "A").
        ``weekday`` is the English 3-letter code (``mon``..``sun``).

        Returns ``True`` when no sheet is configured or the read fails.
        """
        day_map = {
            "mon": "LUNES", "tue": "MARTES", "wed": "MIERCOLES",
            "thu": "JUEVES", "fri": "VIERNES", "sat": "SABADO", "sun": "DOMINGO",
        }
        sheet_day = day_map.get(weekday.lower(), weekday.upper())
        if sheet_day not in WEEKDAYS:
            return True  # weekend → assume active unless sheet says otherwise

        try:
            status = self.fetch_barber_weekly_status()
        except SheetsReadError:
            return True

        barber_data = status.get(barber_code.upper(), {})
        return barber_data.get(sheet_day, True)

    def fetch_service_catalog(self) -> list[dict[str, Any]]:
        """Read the service catalog from the SERVICIOS tab.

        Expected layout (header row 1):
            Code | Name | Duration (min) | Price | Active

        Returns a list of service dicts. Returns an empty list on any
        read failure so the caller does not break.
        """
        try:
            raw_range = f"{self._service_tab}!{self._service_range}"
            url = self.BASE_URL.format(
                sid=self._spreadsheet_id, range=raw_range
            )
            params = self._build_params()
            resp = self._client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            rows = data.get("values", [])
            if not rows or len(rows) < 2:
                return []

            # Find header row
            header = [h.strip().upper() for h in rows[0]]
            code_idx = self._find_col(header, "CODIGO", "CODE")
            name_idx = self._find_col(header, "NOMBRE", "NAME")
            dur_idx = self._find_col(header, "DURACION", "DURATION")
            price_idx = self._find_col(header, "PRECIO", "PRICE")
            active_idx = self._find_col(header, "ACTIVO", "ACTIVE")

            services: list[dict[str, Any]] = []
            for row in rows[1:]:
                if not row or not row[0].strip():
                    continue
                svc = {}
                if code_idx is not None and code_idx < len(row):
                    svc["code"] = row[code_idx].strip()
                if name_idx is not None and name_idx < len(row):
                    svc["name"] = row[name_idx].strip()
                if dur_idx is not None and dur_idx < len(row):
                    try:
                        svc["duration_minutes"] = int(row[dur_idx].strip())
                    except (ValueError, TypeError):
                        svc["duration_minutes"] = 30
                if price_idx is not None and price_idx < len(row):
                    try:
                        svc["price_cents"] = int(float(row[price_idx].strip()) * 100)
                    except (ValueError, TypeError):
                        svc["price_cents"] = 0
                if active_idx is not None and active_idx < len(row):
                    svc["is_active"] = row[active_idx].strip().lower() in ACTIVE_VALUES
                else:
                    svc["is_active"] = True
                services.append(svc)

            logger.info(
                "[sheets_reader] service catalog: %d services read",
                len(services),
            )
            return services
        except SheetsReadError:
            logger.exception("[sheets_reader] failed to read service catalog")
            return []
        except Exception:
            logger.exception("[sheets_reader] unexpected error reading services")
            return []

    def fetch_operational_state(self) -> dict[str, Any]:
        """Return a combined snapshot of sheet-driven operational state.

        Convenience for the runtime mode endpoint.
        """
        bot = self.fetch_bot_status()
        barbers = self.fetch_barber_weekly_status()
        return {
            "bot_enabled": bot,
            "barbers_weekly": barbers,
            "source": "google_sheets",
            "constraints": {
                "read_only": True,
                "auth_type": "api_key" if self._api_key else "public_sheet",
                "write_back": False,
            },
        }

    # ── Internal helpers ────────────────────────────────────────────────

    def _fetch_config_rows(self) -> list[list[str]]:
        """Fetch the CONFIG tab values from the Sheets API.

        Returns rows as a list of string lists. Raises
        ``SheetsReadError`` on failure.
        """
        raw_range = f"{self._config_tab}!{self._config_range}"
        url = self.BASE_URL.format(
            sid=self._spreadsheet_id, range=raw_range
        )
        params = self._build_params()
        try:
            resp = self._client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            return data.get("values", [])
        except httpx.HTTPStatusError as exc:
            raise SheetsReadError(
                f"Sheets API {exc.response.status_code}: {exc.response.text[:300]}"
            ) from exc
        except httpx.RequestError as exc:
            raise SheetsReadError(f"Request failed: {exc}") from exc
        except ValueError as exc:
            raise SheetsReadError(f"Invalid JSON response: {exc}") from exc

    def _build_params(self) -> dict[str, str]:
        params: dict[str, str] = {}
        if self._api_key:
            params["key"] = self._api_key
        return params

    @staticmethod
    def _find_col(header: list[str], *candidates: str) -> int | None:
        """Find the column index for one of the candidate header names.

        Returns the first matching index, or None if none match.
        Header comparison is case-insensitive.
        """
        for i, col in enumerate(header):
            if col in candidates:
                return i
        return None

    def close(self) -> None:
        self._client.close()

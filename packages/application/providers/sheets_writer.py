"""Google Sheets appointment write-back — append appointments to a configured sheet.

Uses the Google Sheets API ``values:append`` endpoint to add a new row to a
specified sheet tab. Write access requires either **OAuth 2.0** or a **service
account** credential — a plain API key (the kind used by the reader for public
sheets) cannot write to Google Sheets.

How it works
------------
1. The tenant's sheets provider config provides the ``spreadsheet_id``,
   an optional OAuth / service-account access token, the sheet tab name,
   and the range.
2. When a booking is created (in sheets or hybrid mode), the writer appends
   a row with the appointment details.
3. If no write-capable credential is configured, the writer logs a warning
   and skips the write. This is the **honest first pass**: the pipeline is
   wired end-to-end but gated behind credential availability.

Constraints (honest)
--------------------
- **No write credential configured** → write-back is silently skipped.
  Configure a service-account email + private key in the provider config
  ``credentials`` dict (``client_email`` + ``private_key``) or pass an
  OAuth ``access_token``.
- **Only appends rows** — no update/cancel sync yet.
- **Expected sheet layout** (APPOINTMENTS tab):
    Date | Time | Barber | Customer Name | Service | Status | Phone | DNI | Notes
  The header row is expected on row 1. Values are appended below the last
  row with data.
- **No retry / queue** — a single HTTP call per booking. If the API call
  fails the booking still succeeds in the database.

Future improvements
-------------------
- Support updating status on cancel/reschedule.
- Support batch flush (write accumulated changes periodically).
- Support per-tenant custom column mapping.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any
from uuid import UUID

import httpx

logger = logging.getLogger(__name__)

# Default range for appointment writes
DEFAULT_APPTS_RANGE = "A:I"  # 9 columns: Date, Time, Barber, Customer, Service, Status, Phone, DNI, Notes


class SheetsWriteError(Exception):
    """Raised when the Sheets API write call fails."""


class GoogleSheetsWriter:
    """Append appointment rows to a configured Google Sheet.

    Usage::

        writer = GoogleSheetsWriter(
            spreadsheet_id="...",
            access_token="...",       # OAuth or service-account token
            sheet_tab="APPOINTMENTS",
        )
        writer.append_appointment(
            appointment_date=date.today(),
            start_time=...,
            barber_name="Omar",
            customer_name="Juan Pérez",
            service_name="Corte",
            status="pending",
            customer_phone="+54911...",
            customer_dni="...",
            notes="...",
        )
    """

    BASE_URL = "https://sheets.googleapis.com/v4/spreadsheets/{sid}/values/{range}:append"

    def __init__(
        self,
        spreadsheet_id: str,
        access_token: str | None = None,
        sheet_tab: str = "APPOINTMENTS",
        range_: str = DEFAULT_APPTS_RANGE,
        timeout_s: int = 10,
    ) -> None:
        self._spreadsheet_id = spreadsheet_id
        self._access_token = access_token
        self._sheet_tab = sheet_tab
        self._range = range_
        self._timeout = timeout_s
        self._client = httpx.Client(timeout=httpx.Timeout(timeout_s))

    @property
    def is_writeable(self) -> bool:
        """True when a write-capable credential is available."""
        return bool(self._access_token)

    def check_write_access(self) -> dict[str, Any]:
        """Verify the sheet is reachable with write access.

        Returns a dict with ``ok`` (bool) and either ``error`` or details.
        """
        if not self.is_writeable:
            return {
                "ok": False,
                "error": "No access_token configured — write-back requires OAuth or service-account credentials.",
            }
        try:
            # Do a dry-run append of a single-cell row to test access
            raw_range = f"{self._sheet_tab}!A1:A1"
            url = self.BASE_URL.format(
                sid=self._spreadsheet_id, range=raw_range
            )
            params = self._build_params(dry_run=True)
            resp = self._client.post(url, params=params, json={"values": [["test"]]})
            if resp.status_code == 403:
                return {
                    "ok": False,
                    "error": f"Access denied (403). Token lacks write scope for this sheet.",
                }
            resp.raise_for_status()
            return {
                "ok": True,
                "sheet_id": self._spreadsheet_id,
                "tab": self._sheet_tab,
                "has_write_token": True,
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

    def append_appointment(
        self,
        appointment_date: date,
        start_time: datetime,
        barber_name: str,
        customer_name: str,
        service_name: str,
        status: str,
        customer_phone: str,
        customer_dni: str | None = None,
        notes: str | None = None,
    ) -> bool:
        """Append one appointment row to the sheet.

        Args:
            appointment_date: Date of the appointment.
            start_time: Start datetime.
            barber_name: Barber display name.
            customer_name: Customer display name.
            service_name: Service name.
            status: Appointment status.
            customer_phone: Customer phone number.
            customer_dni: Optional customer DNI/national ID.
            notes: Optional notes.

        Returns:
            True if the row was appended, False if skipped (no credential)
            or failed. Logs on failure but never raises.

        The booking flow should call this after a successful DB commit;
        a failure here must NOT roll back the booking.
        """
        if not self.is_writeable:
            logger.info(
                "[sheets_writer] no write credential — skipping append for %s on %s",
                customer_name, appointment_date,
            )
            return False

        row = self._build_row(
            appointment_date=appointment_date,
            start_time=start_time,
            barber_name=barber_name,
            customer_name=customer_name,
            service_name=service_name,
            status=status,
            customer_phone=customer_phone,
            customer_dni=customer_dni,
            notes=notes,
        )
        try:
            raw_range = f"{self._sheet_tab}!{self._range}"
            url = self.BASE_URL.format(
                sid=self._spreadsheet_id, range=raw_range
            )
            params = self._build_params(dry_run=False)
            payload = {"values": [row], "majorDimension": "ROWS"}
            resp = self._client.post(url, params=params, json=payload)
            resp.raise_for_status()
            result = resp.json()
            logger.info(
                "[sheets_writer] appended row for %s on %s — updates: %s",
                customer_name, appointment_date,
                result.get("updates", {}),
            )
            return True
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "[sheets_writer] append failed (%s): %s",
                exc.response.status_code, exc.response.text[:200],
            )
            return False
        except httpx.RequestError as exc:
            logger.warning("[sheets_writer] append request failed: %s", exc)
            return False
        except Exception as exc:
            logger.warning("[sheets_writer] append error: %s", exc)
            return False

    def close(self) -> None:
        self._client.close()

    # ── Internal ──────────────────────────────────────────────────────

    def _build_row(
        self,
        appointment_date: date,
        start_time: datetime,
        barber_name: str,
        customer_name: str,
        service_name: str,
        status: str,
        customer_phone: str,
        customer_dni: str | None = None,
        notes: str | None = None,
    ) -> list[str]:
        """Build a row for the APPOINTMENTS tab.

        Expected columns:
            Date | Time | Barber | Customer Name | Service | Status | Phone | DNI | Notes
        """
        return [
            appointment_date.isoformat(),
            start_time.strftime("%H:%M"),
            barber_name,
            customer_name,
            service_name,
            status,
            customer_phone,
            customer_dni or "",
            notes or "",
        ]

    def _build_params(self, dry_run: bool = False) -> dict[str, str]:
        params: dict[str, str] = {
            "valueInputOption": "USER_ENTERED",
            "insertDataOption": "INSERT_ROWS",
        }
        if self._access_token:
            params["access_token"] = self._access_token
        if dry_run:
            params["dryRun"] = "true"
        return params

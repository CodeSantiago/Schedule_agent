"""Tests for the GoogleSheetsReader.

These tests use mocked HTTP responses to avoid real API calls.
They verify the parsing logic for the CONFIG tab layout.
"""

from __future__ import annotations

from unittest.mock import Mock, patch

import httpx
import pytest

from packages.application.providers.sheets_reader import (
    GoogleSheetsReader,
    SheetsReadError,
)


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture()
def reader():
    """A reader instance with a dummy spreadsheet ID (no real API key)."""
    return GoogleSheetsReader(
        spreadsheet_id="test_sheet_id",
        api_key="test_api_key",
        config_tab="CONFIG",
        config_range="A1:G30",
    )


# ── Mock helpers ────────────────────────────────────────────────────────


def _mock_response(status=200, json_data=None):
    mock = Mock()
    mock.status_code = status
    mock.json.return_value = json_data or {}
    mock.text = str(json_data or {})
    if status >= 400:
        import httpx
        mock.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status}",
            request=Mock(),
            response=mock,
        )
    else:
        mock.raise_for_status.return_value = None
    return mock


def _bot_active_rows():
    """CONFIG rows where bot is enabled (activo)."""
    return [
        ["CONFIGURACIÓN", "", "", "", "", "", ""],
        ["", "", "", "", "", "", ""],
        ["BOT", "activo", "", "", "", "", ""],
        ["", "", "", "", "", "", ""],
        ["BARBERO", "LUNES", "MARTES", "MIERCOLES", "JUEVES", "VIERNES", ""],
        ["O (Omar)", "activo", "activo", "activo", "activo", "activo", ""],
        ["R (Rodrigo)", "activo", "ausente", "activo", "activo", "activo", ""],
        ["A (Agustín)", "activo", "activo", "activo", "ausente", "activo", ""],
    ]


def _bot_disabled_rows():
    """CONFIG rows where bot is disabled (apagado)."""
    rows = _bot_active_rows()
    rows[2][1] = "apagado"
    return rows


# ── Tests: bot status ────────────────────────────────────────────────────


class TestFetchBotStatus:
    def test_bot_active(self, reader):
        """Bot status from Config tab -> True when 'activo'."""
        with patch.object(reader._client, "get", return_value=_mock_response(
            json_data={"values": _bot_active_rows()}
        )):
            assert reader.fetch_bot_status() is True

    def test_bot_disabled(self, reader):
        """Bot status -> False when 'apagado'."""
        with patch.object(reader._client, "get", return_value=_mock_response(
            json_data={"values": _bot_disabled_rows()}
        )):
            assert reader.fetch_bot_status() is False

    def test_bot_missing_row_defaults_to_active(self, reader):
        """Missing BOT row -> True (safe default)."""
        rows = [
            ["SOME", "DATA", "", "", "", "", ""],
            ["OTHER", "ROW", "", "", "", "", ""],
        ]
        with patch.object(reader._client, "get", return_value=_mock_response(
            json_data={"values": rows}
        )):
            assert reader.fetch_bot_status() is True

    def test_bot_api_error_defaults_to_active(self, reader):
        """HTTP error -> True (safe default)."""
        with patch.object(reader._client, "get", return_value=_mock_response(
            status=403,
            json_data={"error": "Access Denied"},
        )):
            assert reader.fetch_bot_status() is True

    def test_bot_synonyms(self, reader):
        """Multiple active synonyms work."""
        for val in ["active", "on", "true", "si", "yes", "activo"]:
            rows = _bot_active_rows()
            rows[2][1] = val
            rows[2][2] = ""
            with patch.object(reader._client, "get", return_value=_mock_response(
                json_data={"values": rows}
            )):
                assert reader.fetch_bot_status() is True, f"value={val!r} should be active"

    def test_bot_inactive_synonyms(self, reader):
        """Multiple inactive synonyms work."""
        for val in ["inactivo", "apagado", "off", "false", "no", "ausente"]:
            rows = _bot_active_rows()
            rows[2][1] = val
            with patch.object(reader._client, "get", return_value=_mock_response(
                json_data={"values": rows}
            )):
                assert reader.fetch_bot_status() is False, f"value={val!r} should be inactive"


# ── Tests: barber weekly status ─────────────────────────────────────────


class TestFetchBarberWeeklyStatus:
    def test_parses_barber_rows(self, reader):
        """Correctly parse barber status from CONFIG rows."""
        with patch.object(reader._client, "get", return_value=_mock_response(
            json_data={"values": _bot_active_rows()}
        )):
            status = reader.fetch_barber_weekly_status()

        assert "O" in status
        assert "R" in status
        assert "A" in status

        # Omar: all active
        assert status["O"]["LUNES"] is True
        assert status["O"]["VIERNES"] is True

        # Rodrigo: absent on TUESDAY
        assert status["R"]["LUNES"] is True
        assert status["R"]["MARTES"] is False
        assert status["R"]["MIERCOLES"] is True

        # Agustín: absent on THURSDAY
        assert status["A"]["JUEVES"] is False
        assert status["A"]["VIERNES"] is True

    def test_empty_sheet_returns_empty(self, reader):
        """Empty values -> empty dict."""
        with patch.object(reader._client, "get", return_value=_mock_response(
            json_data={"values": []}
        )):
            assert reader.fetch_barber_weekly_status() == {}

    def test_no_barbero_header(self, reader):
        """Sheet without BARBERO header -> empty dict."""
        rows = [["SOME", "DATA"], ["OTHER", "VALUES"]]
        with patch.object(reader._client, "get", return_value=_mock_response(
            json_data={"values": rows}
        )):
            assert reader.fetch_barber_weekly_status() == {}

    def test_api_error_returns_empty(self, reader):
        """HTTP error -> empty dict."""
        with patch.object(reader._client, "get", return_value=_mock_response(
            status=500, json_data={"error": "Internal"}
        )):
            assert reader.fetch_barber_weekly_status() == {}


# ── Tests: is_barber_active_on_day ──────────────────────────────────────


class TestIsBarberActiveOnDay:
    def test_active_barber(self, reader):
        """Barber with 'activo' on the day -> True."""
        with patch.object(reader, "fetch_barber_weekly_status", return_value={
            "O": {"LUNES": True, "MARTES": True, "MIERCOLES": True, "JUEVES": True, "VIERNES": True},
        }):
            assert reader.is_barber_active_on_day("O", "LUNES") is True

    def test_inactive_barber(self, reader):
        """Barber with 'ausente' on the day -> False."""
        with patch.object(reader, "fetch_barber_weekly_status", return_value={
            "R": {"LUNES": True, "MARTES": False, "MIERCOLES": True, "JUEVES": True, "VIERNES": True},
        }):
            assert reader.is_barber_active_on_day("R", "MARTES") is False

    def test_unknown_barber_defaults_to_active(self, reader):
        """Barber not in status dict -> True (safe default)."""
        with patch.object(reader, "fetch_barber_weekly_status", return_value={}):
            assert reader.is_barber_active_on_day("Z", "LUNES") is True

    def test_weekday_not_in_mapping_defaults_to_active(self, reader):
        """Weekend day (SAT) -> True."""
        with patch.object(reader, "fetch_barber_weekly_status", return_value={}):
            assert reader.is_barber_active_on_day("O", "SAT") is True

    def test_read_error_defaults_to_active(self, reader):
        """If fetch_barber_weekly_status raises, default to True."""
        with patch.object(reader, "fetch_barber_weekly_status", side_effect=SheetsReadError("fail")):
            assert reader.is_barber_active_on_day("O", "LUNES") is True


# ── Tests: check_connection ─────────────────────────────────────────────


class TestCheckConnection:
    def test_ok(self, reader):
        """Successful API call returns ok=True."""
        with patch.object(reader._client, "get", return_value=_mock_response(
            json_data={"values": [["test"]]}
        )):
            result = reader.check_connection()
            assert result["ok"] is True
            assert result["has_api_key"] is True
            assert result["sheet_id"] == "test_sheet_id"

    def test_http_error(self, reader):
        """HTTP error returns ok=False."""
        with patch.object(reader._client, "get", return_value=_mock_response(
            status=404, json_data={"error": "Not found"}
        )):
            result = reader.check_connection()
            assert result["ok"] is False
            assert "404" in result["error"]

    def test_no_api_key(self, reader):
        """Reader without API key reports has_api_key=False."""
        no_key_reader = GoogleSheetsReader(spreadsheet_id="test")
        with patch.object(no_key_reader._client, "get", return_value=_mock_response(
            json_data={"values": [["test"]]}
        )):
            result = no_key_reader.check_connection()
            assert result["ok"] is True
            assert result["has_api_key"] is False


# ── Tests: fetch_operational_state ──────────────────────────────────────


class TestFetchOperationalState:
    def test_returns_combined_state(self, reader):
        """Returns dict with bot, barbers, source, and constraints."""
        with patch.object(reader._client, "get", return_value=_mock_response(
            json_data={"values": _bot_active_rows()}
        )):
            state = reader.fetch_operational_state()

        assert state["bot_enabled"] is True
        assert "barbers_weekly" in state
        assert state["source"] == "google_sheets"
        assert state["constraints"]["read_only"] is True
        assert state["constraints"]["write_back"] is False

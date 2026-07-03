"""Tests for ``packages.application.providers.kapso_config``.

Covers the global-key resolution, credential/settings normalisation,
validation, and the transport-readiness seam.
"""

from __future__ import annotations

import os

import pytest

from packages.application.providers.kapso_config import (
    KAPSO_CREDENTIAL_KEYS,
    KAPSO_SETTINGS_KEYS,
    compute_kapso_credentials,
    compute_kapso_settings,
    get_kapso_platform_config,
    read_kapso_transport_config,
    resolve_api_key,
    validate_kapso_config,
)


# ── resolve_api_key ────────────────────────────────────────────────────────


class TestResolveApiKey:
    def test_global_ref_uses_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KAPSO_API_KEY", "env-super-secret")
        assert resolve_api_key("global") == "env-super-secret"

    def test_global_ref_uses_explicit_arg(self) -> None:
        assert resolve_api_key("global", global_key="arg-key") == "arg-key"

    def test_global_ref_arg_beats_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("KAPSO_API_KEY", "env-key")
        assert resolve_api_key("global", global_key="arg-key") == "arg-key"

    def test_per_tenant_ref_returns_ref_directly(self) -> None:
        assert resolve_api_key("pk_test_abc123") == "pk_test_abc123"

    def test_per_tenant_ref_beats_everything(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("KAPSO_API_KEY", "env-key")
        assert (
            resolve_api_key("pk_override", global_key="arg-key")
            == "pk_override"
        )

    def test_empty_ref_falls_back_to_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("KAPSO_API_KEY", "env-fallback")
        assert resolve_api_key("") == "env-fallback"

    def test_none_ref_falls_back_to_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("KAPSO_API_KEY", "env-fallback")
        assert resolve_api_key(None) == "env-fallback"

    def test_nothing_available_returns_none(self) -> None:
        old = os.environ.pop("KAPSO_API_KEY", None)
        try:
            assert resolve_api_key(None) is None
            assert resolve_api_key("") is None
            assert resolve_api_key("global") is None
        finally:
            if old is not None:
                os.environ["KAPSO_API_KEY"] = old

    def test_whitespace_only_ref_treated_as_global(self) -> None:
        assert resolve_api_key("  ") is None  # no env set

    def test_global_case_insensitive(self) -> None:
        assert resolve_api_key("GLOBAL") is None  # no env set


# ── get_kapso_platform_config ──────────────────────────────────────────────


class TestGetKapsoPlatformConfig:
    def test_returns_both_when_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KAPSO_API_KEY", "pk_test_key")
        monkeypatch.setenv("KAPSO_BASE_URL", "https://custom.kapso.ai")
        cfg = get_kapso_platform_config()
        assert cfg == {"api_key": "pk_test_key", "base_url": "https://custom.kapso.ai"}

    def test_returns_empty_when_nothing_set(self) -> None:
        old_key = os.environ.pop("KAPSO_API_KEY", None)
        old_url = os.environ.pop("KAPSO_BASE_URL", None)
        try:
            assert get_kapso_platform_config() == {}
        finally:
            if old_key is not None:
                os.environ["KAPSO_API_KEY"] = old_key
            if old_url is not None:
                os.environ["KAPSO_BASE_URL"] = old_url

    def test_returns_partial_when_only_api_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("KAPSO_API_KEY", "only-key")
        cfg = get_kapso_platform_config()
        assert cfg == {"api_key": "only-key"}


# ── compute_kapso_credentials ──────────────────────────────────────────────


class TestComputeKapsoCredentials:
    def test_resolves_global_ref(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KAPSO_API_KEY", "env-global")
        result = compute_kapso_credentials({"api_key_ref": "global"})
        assert result == {"api_key": "env-global"}

    def test_resolves_per_tenant_ref(self) -> None:
        result = compute_kapso_credentials({"api_key_ref": "pk_tenant"})
        assert result == {"api_key": "pk_tenant"}

    def test_includes_webhook_secret(self) -> None:
        result = compute_kapso_credentials(
            {"api_key_ref": "global", "webhook_secret": "whsec_abc"}
        )
        assert result.get("webhook_secret") == "whsec_abc"

    def test_omits_empty_webhook_secret(self) -> None:
        result = compute_kapso_credentials({"api_key_ref": "global", "webhook_secret": ""})
        assert "webhook_secret" not in result

    def test_empty_credentials_dict(self) -> None:
        result = compute_kapso_credentials({})
        assert result == {}

    def test_none_credentials(self) -> None:
        result = compute_kapso_credentials(None)
        assert result == {}

    def test_direct_api_key_takes_priority(self) -> None:
        """Direct ``api_key`` value wins over ``api_key_ref`` resolution."""
        result = compute_kapso_credentials(
            {"api_key_ref": "global", "api_key": "direct-key", "foo": "bar"},
            global_api_key="resolved-key",
        )
        assert "api_key" in result
        assert result["api_key"] == "direct-key"  # direct beats ref
        assert "foo" not in result  # not a Kapso key

    def test_explicit_global_key_arg(self) -> None:
        result = compute_kapso_credentials(
            {"api_key_ref": "global"}, global_api_key="arg-key"
        )
        assert result == {"api_key": "arg-key"}


# ── compute_kapso_settings ─────────────────────────────────────────────────


class TestComputeKapsoSettings:
    def test_keeps_only_known_keys(self) -> None:
        result = compute_kapso_settings(
            {
                "phone_number_id": "123",
                "business_account_id": "456",
                "unknown_key": "should_drop",
            }
        )
        assert result == {
            "phone_number_id": "123",
            "business_account_id": "456",
        }

    def test_drops_empty_values(self) -> None:
        result = compute_kapso_settings(
            {"phone_number_id": "", "business_account_id": None, "display_phone_number": "set"}
        )
        assert "phone_number_id" not in result
        assert "business_account_id" not in result
        assert result["display_phone_number"] == "set"

    def test_returns_full_shape(self) -> None:
        full = {
            "provider": "kapso",
            "api_version": "v22.0",
            "base_url": "https://graph.facebook.com/v22.0",
            "platform_base_url": "https://api.kapso.ai",
            "phone_number_id": "109283746152345",
            "business_account_id": "987654321",
            "customer_id": "cust_001",
            "external_customer_id": "ext_001",
            "display_phone_number": "+1234567890",
            "sandbox": True,
            "webhook_id": "wh_001",
            "webhook_kind": "cloud_api",
            "webhook_payload_version": "v1",
            "webhook_events": "messages,message_deliveries",
        }
        result = compute_kapso_settings(full)
        assert result == full

    def test_none_settings(self) -> None:
        assert compute_kapso_settings(None) == {}


# ── validate_kapso_config ──────────────────────────────────────────────────


class TestValidateKapsoConfig:
    def test_empty_config_returns_warnings(self) -> None:
        warnings = validate_kapso_config({}, {})
        assert len(warnings) >= 2  # no api_key, no phone_number_id, etc.

    def test_strict_mode_marks_required(self) -> None:
        warnings = validate_kapso_config({}, {}, strict=True)
        required_warnings = [w for w in warnings if "required" in w]
        assert len(required_warnings) >= 2  # phone_number_id, business_account_id

    def test_full_valid_config_returns_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("KAPSO_API_KEY", "pk_global")
        warnings = validate_kapso_config(
            {"api_key_ref": "global"},
            {
                "phone_number_id": "123",
                "business_account_id": "456",
                "display_phone_number": "+1234567890",
            },
        )
        assert warnings == []

    def test_missing_display_phone(self) -> None:
        old_key = os.environ.pop("KAPSO_API_KEY", None)
        try:
            warnings = validate_kapso_config(
                {"api_key_ref": "pk_test"},
                {"phone_number_id": "123", "business_account_id": "456"},
            )
            messages = [w for w in warnings if "display_phone_number" in w]
            assert len(messages) == 1
        finally:
            if old_key is not None:
                os.environ["KAPSO_API_KEY"] = old_key


# ── read_kapso_transport_config ────────────────────────────────────────────


class TestReadKapsoTransportConfig:
    def test_resolves_global_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KAPSO_API_KEY", "env-global")
        cfg = read_kapso_transport_config(
            {"api_key_ref": "global", "webhook_secret": "whsec_xyz"},
            {"phone_number_id": "123", "business_account_id": "456"},
        )
        assert cfg["api_key"] == "env-global"
        assert cfg["webhook_secret"] == "whsec_xyz"
        assert cfg["phone_number_id"] == "123"
        assert cfg["business_account_id"] == "456"

    def test_returns_full_expected_shape(self) -> None:
        """The transport config must contain every key a KapsoTransport
        constructor would destructure."""
        cfg = read_kapso_transport_config(
            {"api_key_ref": "pk_tenant", "webhook_secret": "whsec_abc"},
            {
                "api_version": "v22.0",
                "base_url": "https://graph.facebook.com/v22.0",
                "platform_base_url": "https://api.kapso.ai",
                "phone_number_id": "109283746152345",
                "business_account_id": "987654321",
                "display_phone_number": "+1234567890",
                "sandbox": True,
                "webhook_id": "wh_001",
                "webhook_events": "messages",
                "customer_id": "cust_001",
                "external_customer_id": "ext_001",
            },
        )
        assert cfg == {
            "api_key": "pk_tenant",
            "webhook_secret": "whsec_abc",
            "api_version": "v22.0",
            "base_url": "https://graph.facebook.com/v22.0",
            "platform_base_url": "https://api.kapso.ai",
            "phone_number_id": "109283746152345",
            "business_account_id": "987654321",
            "display_phone_number": "+1234567890",
            "sandbox": True,
            "webhook_id": "wh_001",
            "webhook_events": "messages",
            "customer_id": "cust_001",
            "external_customer_id": "ext_001",
        }

    def test_minimal_config(self) -> None:
        """Only api_key is required in credentials; the rest is additive."""
        cfg = read_kapso_transport_config(
            {"api_key_ref": "pk_minimal"},
            {"phone_number_id": "111", "business_account_id": "222"},
        )
        assert cfg == {
            "api_key": "pk_minimal",
            "phone_number_id": "111",
            "business_account_id": "222",
        }

    def test_explicit_global_key_arg(self) -> None:
        cfg = read_kapso_transport_config(
            {"api_key_ref": "global"},
            {"phone_number_id": "123"},
            global_api_key="explicit-key",
        )
        assert cfg["api_key"] == "explicit-key"


# ── Canonical key sets ─────────────────────────────────────────────────────


class TestKnownKeys:
    def test_credential_keys_are_frozen(self) -> None:
        # frozenset raises AttributeError for .add(), confirming it's immutable
        with pytest.raises(AttributeError):
            KAPSO_CREDENTIAL_KEYS.add("extra")  # type: ignore[attr-defined]

    def test_settings_keys_are_frozen(self) -> None:
        with pytest.raises(AttributeError):
            KAPSO_SETTINGS_KEYS.add("extra")  # type: ignore[attr-defined]

    def test_api_key_ref_in_credential_keys(self) -> None:
        assert "api_key_ref" in KAPSO_CREDENTIAL_KEYS

    def test_phone_number_id_in_settings_keys(self) -> None:
        assert "phone_number_id" in KAPSO_SETTINGS_KEYS

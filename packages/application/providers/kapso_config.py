"""Kapso-specific provider-config normalization helpers.

This module is the single source of truth for the Kapso config shape.
It bridges the generic ``provider_configs.credentials`` and ``.settings``
JSON columns with the Kapso-specific fields a real ``KapsoTransport``
adapter will need.

Global key pattern
------------------
The platform-level ``KAPSO_API_KEY`` env var is the **global default**.
Each tenant's ``provider_configs`` row can reference it by setting
``credentials.api_key_ref`` to ``"global"`` (or leaving it blank).
A per-tenant override is stored as the literal value of
``credentials.api_key_ref``.

    credentials: { "api_key_ref": "global",  "webhook_secret": "..." }
    settings:    { "phone_number_id": "109283746152345", ... }

The ``KapsoTransport(…)`` calls ``read_kapso_transport_config``
to get a flat, resolved parameter dict — it does NOT need to know about
the global/ref distinction.

Webhook signature verification
------------------------------
Kapso signs webhook payloads with HMAC-SHA256 using the tenant's
``webhook_secret``.  The ``verify_webhook_signature`` helper validates
the ``X-Kapso-Signature`` header against the raw request body.
"""

from __future__ import annotations

import os
from typing import Any

# ── Canonical Kapso key sets ──────────────────────────────────────────────
# Every key a Kapso per-tenant config can carry.  Kept as module-level
# frozensets so callers can iterate or test against the known shapes.

KAPSO_CREDENTIAL_KEYS: frozenset[str] = frozenset({
    "api_key",        # direct API key value (simplified sandbox path)
    "api_key_ref",    # "global" (use env)  OR  a per-tenant literal override
    "webhook_secret", # per-tenant webhook shared secret
})

KAPSO_SETTINGS_KEYS: frozenset[str] = frozenset({
    "provider",
    "api_version",
    "base_url",
    "platform_base_url",
    "phone_number_id",
    "business_account_id",
    "customer_id",
    "external_customer_id",
    "display_phone_number",
    "sandbox",         # sandbox/test mode toggle
    "webhook_id",
    "webhook_kind",
    "webhook_payload_version",
    "webhook_events",
})

# ── Public helpers ─────────────────────────────────────────────────────────


def resolve_api_key(
    api_key_ref: str | None,
    *,
    global_key: str | None = None,
) -> str | None:
    """Resolve the effective Kapso API key for a transport call.

    Priority (first non-None wins):
      1. ``api_key_ref`` — when it is set and is NOT ``"global"``
         (treat it as a per-tenant override).
      2. ``global_key`` — the explicit argument.
      3. ``os.environ["KAPSO_API_KEY"]`` — the platform-level env var.
      4. ``None`` — no usable key found.
    """
    ref = (api_key_ref or "").strip()
    if ref and ref.lower() != "global":
        return ref
    if global_key:
        return global_key
    env_key = os.environ.get("KAPSO_API_KEY")
    if env_key:
        return env_key
    return None


def get_kapso_platform_config() -> dict[str, str]:
    """Read platform-level Kapso settings from env vars.

    Returns a dict that may contain ``api_key`` and ``base_url``
    depending on what environment variables are set.
    """
    result: dict[str, str] = {}
    key = os.environ.get("KAPSO_API_KEY", "").strip()
    if key:
        result["api_key"] = key
    url = os.environ.get("KAPSO_BASE_URL", "").strip()
    if url:
        result["base_url"] = url
    return result


def compute_kapso_credentials(
    credentials: dict[str, Any] | None,
    *,
    global_api_key: str | None = None,
) -> dict[str, Any]:
    """Normalize a raw ``credentials`` dict into resolved Kapso credentials.

    Resolves the API key (direct ``api_key`` takes priority, then
    ``api_key_ref`` global/per-tenant pattern) and returns only
    Kapso-relevant items.  This is the dict the future transport
    constructor will destructure.
    """
    raw = dict(credentials or {})
    result: dict[str, Any] = {}

    # Direct api_key has highest priority (simplified sandbox path).
    direct = raw.get("api_key", "").strip()
    if direct:
        result["api_key"] = direct
    else:
        api_key = resolve_api_key(raw.get("api_key_ref"), global_key=global_api_key)
        if api_key:
            result["api_key"] = api_key

    if raw.get("webhook_secret"):
        result["webhook_secret"] = raw["webhook_secret"]

    return result


def compute_kapso_settings(settings: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize a raw ``settings`` dict: keep only Kapso-relevant keys."""
    raw = dict(settings or {})
    return {
        k: raw[k]
        for k in KAPSO_SETTINGS_KEYS
        if k in raw and raw[k] not in (None, "", [])
    }


def validate_kapso_config(
    credentials: dict[str, Any] | None,
    settings: dict[str, Any] | None,
    *,
    strict: bool = False,
) -> list[str]:
    """Validate a Kapso provider config's credentials and settings.

    Returns a list of human-readable warnings (empty = all good).

    In ``strict`` mode, ``phone_number_id`` and ``business_account_id``
    are treated as required.  In non-strict mode they are advisory.
    """
    warnings: list[str] = []
    creds = credentials or {}
    sets = settings or {}

    # --- credentials ---
    direct_key = creds.get("api_key", "").strip()
    ref = creds.get("api_key_ref")
    if not direct_key and not ref and not os.environ.get("KAPSO_API_KEY"):
        warnings.append(
            "API key not configured: set api_key or api_key_ref on the config "
            "or KAPSO_API_KEY in the environment"
        )

    # --- settings ---
    for required_key in ("phone_number_id", "business_account_id"):
        if not sets.get(required_key):
            suffix = " (required)" if strict else ""
            warnings.append(f"{required_key} is not set{suffix}")

    if not sets.get("display_phone_number"):
        warnings.append("display_phone_number is not set")

    return warnings


def read_kapso_transport_config(
    credentials: dict[str, Any] | None,
    settings: dict[str, Any] | None,
    *,
    global_api_key: str | None = None,
) -> dict[str, Any]:
    """Return a flat, resolved config dict for KapsoTransport construction.

    This is the **seam** a real ``KapsoTransport.__init__`` will call::

        class KapsoTransport:
            def __init__(self, config: dict[str, Any]) -> None: ...

        # in TransportFactory._build:
        cfg = read_kapso_transport_config(
            config.credentials, config.settings,
        )
        return KapsoTransport(cfg)

    The returned dict contains every parameter the Kapso adapter needs,
    with the global key already resolved.
    """
    resolved_creds = compute_kapso_credentials(
        credentials, global_api_key=global_api_key,
    )
    resolved_settings = compute_kapso_settings(settings)

    result: dict[str, Any] = {}

    # Credential-derived keys
    for k in ("api_key", "webhook_secret"):
        if k in resolved_creds:
            result[k] = resolved_creds[k]

    # Settings-derived keys
    for k in (
        "api_version",
        "base_url",
        "platform_base_url",
        "phone_number_id",
        "business_account_id",
        "display_phone_number",
        "sandbox",
        "webhook_id",
        "webhook_events",
        "customer_id",
        "external_customer_id",
    ):
        if k in resolved_settings:
            result[k] = resolved_settings[k]

    return result


# ── Webhook signature verification ──────────────────────────────────────────


def verify_webhook_signature(
    *,
    raw_body: bytes,
    signature_header: str | None,
    webhook_secret: str | None,
) -> bool:
    """Validate an HMAC-SHA256 webhook signature.

    Returns ``True`` when:
    - ``webhook_secret`` is ``None`` or empty (verification disabled).
    - The ``signature_header`` matches ``HMAC-SHA256(webhook_secret, raw_body)``.

    Returns ``False`` when:
    - ``webhook_secret`` is set but ``signature_header`` is missing or empty.
    - The computed HMAC does not match the provided signature.

    Kapso signs the raw request body with the per-tenant ``webhook_secret``
    and sends the result as a hex-encoded ``X-Kapso-Signature`` header.
    The ``signature_header`` value is the raw ``X-Kapso-Signature`` header
    (or the equivalent provider-specific signature header).
    """
    if not webhook_secret:
        return True  # verification disabled

    if not signature_header:
        return False  # secret configured but header missing

    import hashlib
    import hmac

    expected = hmac.new(
        webhook_secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header.strip())

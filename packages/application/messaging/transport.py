"""Messaging transport seam.

A `MessageTransport` is the abstraction the intake service uses to
actually send a reply. The default is ``EchoTransport`` (no-op);
``KapsoTransport`` makes real HTTP calls when an HTTP client is
provided. The seam mirrors the ``MessageTransport`` Protocol in
``packages.infrastructure.messaging`` for backward compat with Part 3.

Design rules:

- ``send`` returns a ``SendResult`` — never raises on transport
  failures so the webhook handler can always return 200.
- ``send`` must NEVER raise on transient transport errors; it must
  return a ``SendResult`` with ``delivered=False`` and an ``error``
  field set. The handler logs the failure on the outgoing_messages row
  (status="failed") and the operator can replay manually.
- The transport does NOT commit the session; the handler does.
- Transports are resolved per-request from a ``TransportFactory``
  driven by the tenant's active ``provider_configs`` row for
  ``kind="whatsapp"``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Protocol
from uuid import UUID


@dataclass(frozen=True)
class SendResult:
    """Outcome of a single `send` call.

    `delivered`  — True when the provider accepted the message.
    `provider_message_id`  — the upstream id (if the provider returned one).
    `error`  — a short, human-readable failure reason; None on success.
    """

    delivered: bool
    provider_message_id: Optional[str] = None
    error: Optional[str] = None


class MessageTransport(Protocol):
    """A pluggable messaging adapter.

    Implementations are constructed per-tenant (the `TransportFactory`
    reads the active `provider_configs` row for `kind="whatsapp"` and
    instantiates the matching adapter). They must be cheap to
    construct and stateless across calls.
    """

    def send(
        self,
        *,
        to_phone: str,
        body: str,
        session_id: Optional[UUID] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> SendResult: ...


class EchoTransport:
    """The default no-op transport: never talks to a real provider.

    Returns `delivered=True` and a synthetic `provider_message_id` so
    downstream code can treat the path as a successful send. This is
    the right choice for development, CI, and any environment where
    no real provider is configured.
    """

    def __init__(self, *, provider_name: str = "echo") -> None:
        self._provider_name = provider_name

    def send(
        self,
        *,
        to_phone: str,
        body: str,
        session_id: Optional[UUID] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> SendResult:
        # Synthetic id keeps the audit log unique per call without any
        # upstream round-trip. A real adapter would return the
        # provider's message id.
        import uuid as _uuid

        return SendResult(
            delivered=True,
            provider_message_id=f"echo-{_uuid.uuid4().hex[:12]}",
        )


class KapsoTransport:
    """Kapso WhatsApp Cloud API transport adapter.

    Constructed by ``TransportFactory`` from a resolved config dict
    (see ``read_kapso_transport_config`` in
    ``packages.application.providers.kapso_config``).

    Real HTTP behaviour
    -------------------
    When ``http_client`` is provided (recommended in production), the
    ``send`` method makes a real HTTPS POST to the Kapso / WhatsApp
    Cloud API.  If the API key is empty or missing, the call fails
    clearly with a ``SendResult(delivered=False, error=...)`` — it
    does NOT silently succeed.

    Stub behaviour (default)
    ------------------------
    When ``http_client`` is ``None`` (the default), ``send`` behaves
    like the original stub: it returns ``delivered=True`` with a
    synthetic ``kapso-stub-...`` id and does no network I/O.  This
    preserves backward compatibility for tests and development
    environments where no real provider is configured.

    Testing notes
    -------------
    - ``last_request`` is populated before every HTTP call so tests
      can inspect the prepared url, headers, and payload without
      mocking the transport.
    - Pass a mock ``httpx.Client`` as ``http_client`` (e.g. using
      ``httpx.MockTransport``) to verify real HTTP behaviour in
      tests.
    """

    def __init__(
        self,
        config: dict[str, Any],
        *,
        http_client: Any = None,
    ) -> None:
        self._api_key = config.get("api_key", "")
        self._base_url = (
            config.get("base_url") or "https://api.kapso.ai/meta/whatsapp/v24.0"
        ).rstrip("/")
        self._phone_number_id = config.get("phone_number_id") or ""

        # HTTP client: explicit injection OR lazily created on first
        # ``_do_post`` when an API key is present.  ``None`` means
        # stub mode (no real HTTP).
        self._http_client: Any = http_client

        # Test hook: populated by send() before forwarding to _do_post
        self.last_request: dict[str, Any] | None = None

    def send(
        self,
        *,
        to_phone: str,
        body: str,
        session_id: Optional[UUID] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> SendResult:
        # --- fail closed when no API key is configured --------------------
        if not self._api_key:
            return SendResult(
                delivered=False,
                error="Kapso API key not configured — set api_key "
                "on the provider config or KAPSO_API_KEY in the environment",
            )

        headers = {
            "X-API-Key": self._api_key,
            "Content-Type": "application/json",
        }
        path = (
            f"{self._phone_number_id}/messages"
            if self._phone_number_id
            else "messages"
        )
        url = f"{self._base_url}/{path}"
        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to_phone,
            "type": "text",
            "text": {"preview_url": False, "body": body},
        }
        self.last_request = {
            "url": url,
            "headers": dict(headers),
            "payload": payload,
        }
        return self._do_post(url, headers, payload)

    def _do_post(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> SendResult:
        """POST the prepared request — either real HTTP or stub.

        The HTTP client is created lazily on the first real send when
        an API key is present.  This keeps construction cheap and lets
        tests pass without mocking when no API key is set.
        """
        if self._http_client is not None:
            # Explicitly injected client (e.g. mock in tests).
            return self._real_post(url, headers, payload)
        if self._api_key:
            # API key present but no explicit client → lazy create.
            import httpx as _httpx

            self._http_client = _httpx.Client(timeout=30.0)
            return self._real_post(url, headers, payload)
        return self._stub_post()

    def _real_post(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> SendResult:
        """Real HTTP POST via the injected ``http_client``."""
        try:
            resp = self._http_client.post(
                url, headers=headers, json=payload, timeout=30.0
            )
            if resp.is_success:
                data = resp.json()
                msg_id = (
                    data.get("messages", [{}])[0].get("id")
                    or data.get("message_id")
                    or f"kapso-{resp.status_code}"
                )
                return SendResult(delivered=True, provider_message_id=str(msg_id))
            return SendResult(
                delivered=False,
                error=f"Kapso API returned {resp.status_code}: {resp.text[:200]}",
            )
        except Exception as exc:
            return SendResult(
                delivered=False,
                error=f"Kapso transport error: {exc}",
            )

    def _stub_post(self) -> SendResult:
        """No-op stub: returns success with a synthetic id."""
        import uuid as _uuid

        return SendResult(
            delivered=True,
            provider_message_id=f"kapso-stub-{_uuid.uuid4().hex[:12]}",
        )


class TransportFactory:
    """Resolve the right `MessageTransport` for a tenant.

    Part 4 ships only the `EchoTransport`; the factory checks the
    tenant's `provider_configs` for `kind="whatsapp"` and would
    dispatch to the matching adapter in a future slice (`Kapso`,
    `Twilio`, etc.). When no active config exists — or the
    `provider_name` is unknown — the factory returns the
    `EchoTransport` so behaviour is always well-defined.
    """

    def __init__(self, session_factory) -> None:  # type: ignore[no-untyped-def]
        self._session_factory = session_factory

    def for_tenant(self, tenant_id: UUID) -> MessageTransport:
        # Lazy import: avoid pulling the full model graph at module
        # load (some test paths don't need it).
        from packages.infrastructure.repositories import ProviderConfigRepository
        from packages.infrastructure.db.models.providers import ProviderConfig

        with self._session_factory() as session:
            repo = ProviderConfigRepository(session, tenant_id)
            active = repo.get_active_for_kind("whatsapp")
            if active is None:
                return EchoTransport()
            return self._build(active)

    def _build(self, config: "ProviderConfig") -> MessageTransport:  # type: ignore[name-defined]
        name = (config.provider_name or "").lower().strip()
        if name == "kapso":
            from packages.application.providers.kapso_config import (
                read_kapso_transport_config,
            )

            cfg = read_kapso_transport_config(
                config.credentials,
                config.settings,
            )
            # ``KapsoTransport`` auto-creates a real ``httpx.Client``
            # when ``api_key`` is present — no need to pass one here.
            return KapsoTransport(cfg)
        if name in ("twilio", "whatsapp_cloud", "360dialog"):
            return EchoTransport(provider_name=name)
        return EchoTransport(provider_name=name or "echo")

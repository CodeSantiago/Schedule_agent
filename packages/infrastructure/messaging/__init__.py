"""Messaging adapter interface + a no-op implementation.

A real provider (Kapso, Twilio, WhatsApp Cloud API, ...) plugs in here
behind a small protocol. Part 3 ships:

- `MessageTransport`  — the abstract surface a provider implements.
- `EchoTransport`     — a deterministic in-process transport used as
  the default when no provider has been wired in. It records every
  "send" in-memory and exposes `sent` for tests / smoke checks.
- `build_transport`   — picks a transport for a tenant by inspecting
  the active `provider_configs` row. Unknown adapter names fall back
  to the echo transport so a webhook never blows up just because
  production wiring is incomplete.

The contract is deliberately minimal: `send(to_phone, body) -> str`
returns the provider-side id (or a synthetic one for the echo
transport). The webhook handler does NOT call this directly — the
intake service writes an `outgoing_messages` row and a background
worker (Part 4) is the one that hands the body to a transport. For
Part 3 the seam is wired through so the surface is ready.
"""

from __future__ import annotations

import itertools
import uuid
from dataclasses import dataclass, field
from typing import Protocol

from packages.infrastructure.db.models.providers import ProviderConfig


class MessageTransport(Protocol):
    """A pluggable outbound-messaging backend.

    Implementations are stateless apart from whatever the provider SDK
    keeps internally. The contract is one method: send a body to a
    phone number and return the provider-side message id.
    """

    def send(self, *, to_phone: str, body: str) -> str:
        """Send `body` to `to_phone`. Return the provider-side id."""
        ...


@dataclass
class EchoTransport:
    """A deterministic, in-process transport.

    Every "send" appends a record to `sent`. The synthetic id is a
    monotonically increasing counter (`echo-1`, `echo-2`, ...) so
    tests can assert on ordering.

    Use as the default fallback when a tenant has no `whatsapp`
    provider wired in — keeps webhooks from failing the integration
    in environments where real provider credentials are not yet set.
    """

    sent: list[dict[str, str]] = field(default_factory=list)
    _counter: itertools.count = field(default_factory=lambda: itertools.count(1))

    def send(self, *, to_phone: str, body: str) -> str:
        seq = next(self._counter)
        provider_id = f"echo-{seq}"
        self.sent.append({"to_phone": to_phone, "body": body, "id": provider_id})
        return provider_id


def build_transport(config: ProviderConfig | None) -> MessageTransport:
    """Pick a transport for a tenant based on its active provider config.

    Unknown adapter names (and the "no config" case) fall back to the
    echo transport. This is intentional: the webhook must keep
    working in dev / staging even when the real provider credentials
    are missing.
    """
    if config is None:
        return EchoTransport()
    name = (config.provider_name or "").lower()
    if name in ("echo", "noop", "dev", ""):
        return EchoTransport()
    # Future: kapso, twilio, whatsapp_cloud, etc. Each branch returns
    # a real transport constructed from `config.credentials`.
    return EchoTransport()

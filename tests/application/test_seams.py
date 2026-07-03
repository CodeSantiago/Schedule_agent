"""Tests for the LLM intent seam + messaging transport seam.

The seams are intentionally tiny: a Protocol, a no-op default, a
deterministic-fallback classifier. The goal of these tests is to
pin the *contract* the future real implementations will have to
satisfy, so a future LLM classifier or Kapso adapter can plug in
without rewriting the webhook handler.
"""

from __future__ import annotations

import pytest

from packages.application.intake import (
    DeterministicIntentClassifier,
    IntentClassifier,
    classify_intent,
)
from packages.application.messaging import (
    EchoTransport,
    KapsoTransport,
    MessageTransport,
    SendResult,
    TransportFactory,
)


# --- LLM seam ------------------------------------------------------------


class TestIntentSeam:
    def test_default_classifier_matches_classify_intent(self) -> None:
        impl = DeterministicIntentClassifier()
        for state in ("start", "awaiting_menu", "idle"):
            for text, expected_kind in (
                ("1", "book"),
                ("2", "cancel"),
                ("3", "reschedule"),
                ("nonsense", "unknown"),
            ):
                got = impl.classify(text, current_state=state)
                expected = classify_intent(text, current_state=state)
                assert got.kind == expected.kind == expected_kind
                assert got.next_state == expected.next_state
                assert got.reply == expected.reply

    def test_satisfies_protocol(self) -> None:
        # Static check: any object that exposes `classify` qualifies.
        impl: IntentClassifier = DeterministicIntentClassifier()
        assert impl.classify("1", current_state="start").kind == "book"


# --- Transport seam ------------------------------------------------------


class TestTransportSeam:
    def test_echo_succeeds(self) -> None:
        t: MessageTransport = EchoTransport()
        out = t.send(to_phone="+5491100000000", body="hi")
        assert isinstance(out, SendResult)
        assert out.delivered is True
        assert out.provider_message_id is not None
        assert out.provider_message_id.startswith("echo-")
        assert out.error is None

    def test_factory_returns_echo_when_no_config(self, session) -> None:
        from uuid import uuid4

        f = TransportFactory(lambda: session)
        transport = f.for_tenant(uuid4())
        out = transport.send(to_phone="+5491100000000", body="hi")
        assert out.delivered is True

    def test_factory_returns_echo_for_unknown_provider_name(
        self, session
    ) -> None:
        """A tenant with an active `whatsapp` config whose
        `provider_name` is not yet wired in still gets the echo
        transport — fail open, never block the webhook on a
        misconfigured provider_name.
        """
        from uuid import uuid4

        from packages.infrastructure.db.models.providers import ProviderConfig
        from packages.infrastructure.db.models.tenants import Tenant

        tenant = Tenant(
            id=uuid4(),
            name="T",
            slug=f"t-{uuid4().hex[:6]}",
            status="trial",
            timezone="UTC",
        )
        session.add(tenant)
        session.flush()
        cfg = ProviderConfig(
            id=uuid4(),
            tenant_id=tenant.id,
            kind="whatsapp",
            label="Mystery",
            provider_name="mystery_provider",
            is_active=True,
        )
        session.add(cfg)
        session.commit()

        f = TransportFactory(lambda: session)
        transport = f.for_tenant(tenant.id)
        out = transport.send(to_phone="+5491100000000", body="hi")
        assert out.delivered is True


# --- KapsoTransport seam -------------------------------------------------


class _MockHttpClient:
    """A minimal mock ``httpx.Client`` that records calls and returns
    a fake success response."""

    class _MockResponse:
        status_code = 200
        is_success = True

        def json(self) -> dict:
            return {"messages": [{"id": "kapso-mock-123"}]}

        @property
        def text(self) -> str:
            return '{"messages": [{"id": "kapso-mock-123"}]}'

    def __init__(self) -> None:
        self.last_url: str | None = None
        self.last_headers: dict | None = None
        self.last_json: dict | None = None

    def post(
        self, url: str, *, headers: dict | None = None, json: dict | None = None, timeout: float | None = None  # noqa: ARG002
    ) -> _MockResponse:
        self.last_url = url
        self.last_headers = headers
        self.last_json = json
        return self._MockResponse()


class TestKapsoTransport:
    @staticmethod
    def _make_mock_client() -> _MockHttpClient:
        return _MockHttpClient()

    def test_satisfies_protocol(self) -> None:
        """Structural subtype check: KapsoTransport is a MessageTransport."""
        t: MessageTransport = KapsoTransport({"api_key": "sk_test"})
        assert isinstance(t, KapsoTransport)

    def test_requires_api_key(self) -> None:
        """Missing api_key is not an error — stub without key."""
        t = KapsoTransport({})
        assert t._api_key == ""  # type: ignore[attr-defined]

    def test_prepares_x_api_key_header(self) -> None:
        """The prepared request dict carries X-API-Key."""
        t = KapsoTransport({"api_key": "pk_mykey123"}, http_client=self._make_mock_client())
        t.send(to_phone="+5491111111111", body="hola")
        assert t.last_request is not None
        assert t.last_request["headers"]["X-API-Key"] == "pk_mykey123"

    def test_prepends_bearer_not_bearer(self) -> None:
        """X-API-Key is the raw value, NOT 'Bearer <key>'."""
        t = KapsoTransport({"api_key": "pk_raw"}, http_client=self._make_mock_client())
        t.send(to_phone="+5491100000000", body="test")
        assert t.last_request is not None
        assert t.last_request["headers"]["X-API-Key"] == "pk_raw"
        assert "Bearer" not in t.last_request["headers"]["X-API-Key"]

    def test_prepares_whatsapp_cloud_payload(self) -> None:
        """The payload matches the WhatsApp Cloud API shape."""
        t = KapsoTransport({"api_key": "x", "phone_number_id": "12345"}, http_client=self._make_mock_client())
        t.send(to_phone="+5491100000000", body="Hello!")
        assert t.last_request is not None
        p = t.last_request["payload"]
        assert p["messaging_product"] == "whatsapp"
        assert p["to"] == "+5491100000000"
        assert p["text"]["body"] == "Hello!"

    def test_stub_returns_delivered_with_synthetic_id(self) -> None:
        """No api_key configured → fails clearly with delivered=False."""
        t = KapsoTransport({})  # no api_key → no real HTTP
        out = t.send(to_phone="+5491100000000", body="hi")
        assert out.delivered is False
        assert out.provider_message_id is None
        assert out.error is not None
        assert "not configured" in (out.error or "").lower()

    def test_real_http_path_returns_mock_success(self) -> None:
        """With a mock HTTP client, send() returns delivered=True."""
        t = KapsoTransport({"api_key": "pk_test"}, http_client=self._make_mock_client())
        out = t.send(to_phone="+5491100000000", body="hi")
        assert out.delivered is True
        assert out.provider_message_id == "kapso-mock-123"
        assert out.error is None

    def test_default_base_url(self) -> None:
        """Without explicit base_url, uses the WhatsApp Cloud default."""
        t = KapsoTransport({"api_key": "pk_test"}, http_client=self._make_mock_client())
        t.send(to_phone="+5491100000000", body="hi")
        assert t.last_request is not None
        assert t.last_request["url"].startswith(
            "https://graph.facebook.com/v22.0/"
        )

    def test_custom_base_url(self) -> None:
        """A custom base_url is honoured."""
        t = KapsoTransport(
            {"api_key": "pk_test", "base_url": "https://sandbox.kapso.ai"},
            http_client=self._make_mock_client(),
        )
        t.send(to_phone="+5491100000000", body="hi")
        assert t.last_request is not None
        assert t.last_request["url"].startswith("https://sandbox.kapso.ai/")

    def test_phone_number_id_in_url(self) -> None:
        """When phone_number_id is set, it appears in the URL path."""
        t = KapsoTransport(
            {"api_key": "pk_test", "phone_number_id": "98765"},
            http_client=self._make_mock_client(),
        )
        t.send(to_phone="+5491100000000", body="hi")
        assert t.last_request is not None
        assert "/98765/messages" in t.last_request["url"]

    def test_phone_number_id_missing_uses_messages(self) -> None:
        """Without phone_number_id, just /messages."""
        t = KapsoTransport({"api_key": "pk_test"}, http_client=self._make_mock_client())
        t.send(to_phone="+5491100000000", body="hi")
        assert t.last_request is not None
        assert t.last_request["url"].endswith("/messages")

    def test_missing_api_key_returns_error(self) -> None:
        """No api_key in config returns delivered=False with error."""
        t = KapsoTransport({})
        out = t.send(to_phone="+5491100000000", body="hola")
        assert out.delivered is False
        assert out.error is not None
        assert "api key" in (out.error or "").lower()


class TestTransportFactoryKapsoDispatch:
    def test_returns_kapso_transport_for_kapso_provider(
        self, session, make_tenant
    ) -> None:
        """Factory returns KapsoTransport when provider_name is 'kapso'."""
        from uuid import uuid4

        from packages.infrastructure.db.models.providers import ProviderConfig

        tenant = make_tenant()
        cfg = ProviderConfig(
            id=uuid4(),
            tenant_id=tenant.id,
            kind="whatsapp",
            label="Kapso prod",
            provider_name="kapso",
            credentials={"api_key_ref": "global"},
            settings={"phone_number_id": "12345"},
            is_active=True,
        )
        session.add(cfg)
        session.commit()

        f = TransportFactory(lambda: session)
        transport = f.for_tenant(tenant.id)
        assert isinstance(transport, KapsoTransport)

    def test_kapso_transport_built_from_config(
        self, session, make_tenant, monkeypatch
    ) -> None:
        """KapsoTransport receives resolved credentials from the config."""
        monkeypatch.setenv("KAPSO_API_KEY", "env-global-key")
        import uuid as _uuid

        from packages.infrastructure.db.models.providers import ProviderConfig

        tenant = make_tenant()
        cfg = ProviderConfig(
            id=_uuid.uuid4(),
            tenant_id=tenant.id,
            kind="whatsapp",
            label="Kapso sandbox",
            provider_name="kapso",
            credentials={"api_key_ref": "global"},
            settings={"phone_number_id": "98765"},
            is_active=True,
        )
        session.add(cfg)
        session.commit()

        f = TransportFactory(lambda: session)
        transport = f.for_tenant(tenant.id)
        assert isinstance(transport, KapsoTransport)

        # The resolved env key is in the transport.
        assert transport._api_key == "env-global-key"  # type: ignore[attr-defined]
        # The header is prepared correctly on send (we monkeypatch
        # _do_post to avoid a real HTTP call so the test remains
        # deterministic and dependency-free).
        original_do_post = transport._do_post

        def mock_do_post(
            url: str, headers: dict[str, str], payload: dict[str, object]
        ) -> SendResult:
            return SendResult(
                delivered=True,
                provider_message_id="kapso-mock-123",
            )

        transport._do_post = mock_do_post  # type: ignore[method-assign]
        out = transport.send(to_phone="+5491100000000", body="hi")
        assert out.delivered is True
        assert out.provider_message_id == "kapso-mock-123"
        # Restore so other tests on the same class are unaffected.
        transport._do_post = original_do_post  # type: ignore[method-assign]

        # The X-API-Key header was prepared with the resolved key
        assert transport.last_request is not None
        assert transport.last_request["headers"]["X-API-Key"] == "env-global-key"

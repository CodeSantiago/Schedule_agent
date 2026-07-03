from __future__ import annotations

from sqlalchemy import select

from packages.infrastructure.db.models.messaging import (
    ConversationSession,
    IncomingMessage,
    OutgoingMessage,
)


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def get(self, key: str):
        return self.store.get(key)

    def set(self, key: str, value: str, *, ex=None, nx: bool = False) -> bool:
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    def delete(self, key: str) -> bool:
        return self.store.pop(key, None) is not None

    def compare_and_delete(self, key: str, expected_value: str) -> bool:
        if self.store.get(key) != expected_value:
            return False
        self.store.pop(key, None)
        return True

    def exists(self, key: str) -> bool:
        return key in self.store

    def ping(self) -> bool:
        return True

    def is_available(self) -> bool:
        return True


def test_burst_processing_end_to_end_with_manual_flush(
    client, seeded, api_engine, api_session_factory, monkeypatch
) -> None:
    import apps.api.src.routes.webhooks as webhooks_mod
    import packages.infrastructure.queue as queue_mod
    import packages.infrastructure.redis as redis_mod

    fake_redis = FakeRedis()
    scheduled: list[tuple[object, tuple, dict]] = []

    class FakeQueue:
        def enqueue(self, fn, *args, **kwargs):
            fn(*args, **kwargs)

        def enqueue_in(self, delay_seconds, fn, *args, **kwargs):
            scheduled.append((fn, args, kwargs))

        def shutdown(self, wait: bool = True) -> None:
            pass

    monkeypatch.setattr(redis_mod, "get_redis", lambda: fake_redis)
    monkeypatch.setattr(queue_mod, "get_queue", lambda: FakeQueue())
    monkeypatch.setattr(webhooks_mod, "_BURST_COALESCE_ENABLED", True)
    monkeypatch.setattr(webhooks_mod, "_BURST_WINDOW_SECONDS", 1.2)

    tenant_id = seeded["tenant_id"]
    phone = "+5491100099999"

    first = client.post(
        f"/webhooks/whatsapp/{tenant_id}",
        json={
            "provider_message_id": "coalesce-e2e-1",
            "from_phone": phone,
            "body": "hola",
        },
    )
    second = client.post(
        f"/webhooks/whatsapp/{tenant_id}",
        json={
            "provider_message_id": "coalesce-e2e-2",
            "from_phone": phone,
            "body": "1",
        },
    )

    assert first.json()["state"] == "burst-queued"
    assert second.json()["state"] == "burst-queued"
    assert len(scheduled) == 1

    # Inline processing already advanced the conversation state.
    s = api_session_factory()
    try:
        conv = s.execute(
            select(ConversationSession).where(ConversationSession.tenant_id == tenant_id)
        ).scalar_one()
        incoming = list(
            s.execute(
                select(IncomingMessage)
                .where(IncomingMessage.tenant_id == tenant_id)
                .order_by(IncomingMessage.created_at.asc())
            ).scalars()
        )
        outgoing = list(
            s.execute(
                select(OutgoingMessage)
                .where(OutgoingMessage.tenant_id == tenant_id)
                .order_by(OutgoingMessage.created_at.asc())
            ).scalars()
        )
        assert conv.state == "awaiting_service"
        assert conv.last_message_seq == 2
        assert [m.status for m in incoming] == ["processed", "processed"]
        assert len(outgoing) == 2
        assert all(m.status == "processing" for m in outgoing)
    finally:
        s.close()

    fn, args, kwargs = scheduled[0]
    fn(*args, **kwargs)

    s = api_session_factory()
    try:
        conv = s.execute(
            select(ConversationSession).where(ConversationSession.tenant_id == tenant_id)
        ).scalar_one()
        incoming = list(
            s.execute(
                select(IncomingMessage)
                .where(IncomingMessage.tenant_id == tenant_id)
                .order_by(IncomingMessage.created_at.asc())
            ).scalars()
        )
        outgoing = list(
            s.execute(
                select(OutgoingMessage)
                .where(OutgoingMessage.tenant_id == tenant_id)
                .order_by(OutgoingMessage.created_at.asc())
            ).scalars()
        )

        assert conv.state == "awaiting_service"
        assert conv.last_message_seq == 2
        assert [m.status for m in incoming] == ["processed", "processed"]
        assert len(outgoing) == 2
        sent = [m for m in outgoing if m.status == "sent"]
        assert len(sent) == 1
        assert "servicio" in sent[0].body.lower()
    finally:
        s.close()

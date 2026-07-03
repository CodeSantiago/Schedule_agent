"""Messaging repositories.

Tenant-scoped read/write for `conversation_sessions`, `incoming_messages`
and `outgoing_messages`. The unique constraints on the underlying
tables (`uq_session_per_customer`, `uq_incoming_provider_message`)
keep the rest of the platform from ever producing duplicate work when
a webhook is retried by the provider.

Design notes:

- `ConversationSessionRepository.find_or_create` is the single seam
  the webhook handler uses. It encapsulates the "does the customer
  already have an open session?" check, so the webhook does not have
  to deal with race conditions explicitly — the unique constraint on
  `(tenant_id, channel, customer_phone)` is the source of truth.
- `IncomingMessageRepository.record` is idempotent on
  `(tenant_id, provider_message_id)`. The handler calls it once per
  webhook hit; a retried delivery hits the unique constraint and
  becomes a no-op.
- `OutgoingMessageRepository.record` is just a thin INSERT shim — the
  webhook needs to log the reply it queued for the customer, but the
  actual send happens through the messaging adapter (Part 4). For
  Part 3 we only persist the row.
"""

from __future__ import annotations

import datetime as _dt
import uuid
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from packages.infrastructure.db.models.messaging import (
    ConversationLock,
    ConversationSession,
    IdempotencyKey,
    IncomingMessage,
    OutgoingMessage,
)
from packages.infrastructure.repositories.base import TenantScopedRepository


def _utcnow_naive() -> _dt.datetime:
    """Return current UTC time as a naive datetime.

    SQLite commonly round-trips ``DateTime(timezone=True)`` values as naive
    datetimes, so comparisons inside repository logic should use the same
    representation to avoid offset-aware/naive comparison crashes.
    """
    return _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)


class ConversationSessionRepository(TenantScopedRepository[ConversationSession]):
    model = ConversationSession

    def get_for_customer(
        self, customer_phone: str, channel: str = "whatsapp"
    ) -> ConversationSession | None:
        stmt = (
            self._by_tenant_stmt()
            .where(ConversationSession.customer_phone == customer_phone)
            .where(ConversationSession.channel == channel)
        )
        return self._session.execute(stmt).scalar_one_or_none()

    def find_or_create(
        self,
        customer_phone: str,
        channel: str = "whatsapp",
        *,
        initial_state: str = "start",
    ) -> tuple[ConversationSession, bool]:
        """Return the existing session for (tenant, channel, phone) or
        create a new one. The second tuple element is `True` when a
        new row was created.
        """
        existing = self.get_for_customer(customer_phone, channel)
        if existing is not None:
            return existing, False
        row = ConversationSession(
            id=uuid.uuid4(),
            tenant_id=self._tenant_id,
            channel=channel,
            customer_phone=customer_phone,
            state=initial_state,
            context={},
        )
        self._session.add(row)
        try:
            self._session.flush()
        except IntegrityError:
            # Lost a race: another webhook handler just created the same
            # session. Roll back the half-insert and re-read.
            self._session.rollback()
            found = self.get_for_customer(customer_phone, channel)
            if found is None:  # pragma: no cover - defensive
                raise
            return found, False
        return row, True

    def advance(
        self,
        session: ConversationSession,
        *,
        new_state: str,
        context_patch: dict[str, Any] | None = None,
        bump_seq: bool = True,
    ) -> ConversationSession:
        """Move `session` to `new_state` and optionally merge a context patch.

        Bumping `last_message_seq` is the default — every inbound message
        advances the optimistic-concurrency hint so debuggers can see
        "we processed N messages for this customer".
        """
        session.state = new_state
        if context_patch:
            merged = dict(session.context or {})
            merged.update(context_patch)
            session.context = merged
        if bump_seq:
            session.last_message_seq = (session.last_message_seq or 0) + 1
        self._session.flush()
        return session


class IncomingMessageRepository(TenantScopedRepository[IncomingMessage]):
    model = IncomingMessage

    def get_by_provider_id(
        self, provider_message_id: str
    ) -> IncomingMessage | None:
        stmt = (
            self._by_tenant_stmt()
            .where(IncomingMessage.provider_message_id == provider_message_id)
        )
        return self._session.execute(stmt).scalar_one_or_none()

    def record(
        self,
        *,
        provider_message_id: str,
        from_phone: str,
        body: str,
        channel: str = "whatsapp",
        session_id: UUID | None = None,
        raw_payload: dict[str, Any] | None = None,
    ) -> tuple[IncomingMessage, bool]:
        """Insert an incoming message row, returning `(row, created)`.

        Idempotent on `(tenant_id, provider_message_id)`. When the
        provider retries a webhook, the second hit hits the unique
        constraint and we return the existing row with `created=False`.
        """
        existing = self.get_by_provider_id(provider_message_id)
        if existing is not None:
            return existing, False
        row = IncomingMessage(
            id=uuid.uuid4(),
            tenant_id=self._tenant_id,
            session_id=session_id,
            provider_message_id=provider_message_id,
            channel=channel,
            from_phone=from_phone,
            body=body,
            raw_payload=raw_payload or {},
            status="received",
        )
        self._session.add(row)
        try:
            self._session.flush()
        except IntegrityError:
            self._session.rollback()
            found = self.get_by_provider_id(provider_message_id)
            if found is None:  # pragma: no cover - defensive
                raise
            return found, False
        return row, True

    def list_for_session(self, session_id: UUID) -> list[IncomingMessage]:
        stmt = (
            self._by_tenant_stmt()
            .where(IncomingMessage.session_id == session_id)
            .order_by(IncomingMessage.created_at.asc())
        )
        return list(self._session.execute(stmt).scalars())


class OutgoingMessageRepository(TenantScopedRepository[OutgoingMessage]):
    model = OutgoingMessage

    def record(
        self,
        *,
        to_phone: str,
        body: str,
        session_id: UUID | None = None,
        provider_message_id: str | None = None,
        status: str = "sent",
    ) -> OutgoingMessage:
        """Append a row to the outgoing audit log. Best-effort: a
        failure here MUST NOT block the webhook from returning 200 to
        the provider, so callers wrap in try/except.
        """
        row = OutgoingMessage(
            id=uuid.uuid4(),
            tenant_id=self._tenant_id,
            session_id=session_id,
            to_phone=to_phone,
            body=body,
            provider_message_id=provider_message_id,
            status=status,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def list_for_session(self, session_id: UUID) -> list[OutgoingMessage]:
        stmt = (
            self._by_tenant_stmt()
            .where(OutgoingMessage.session_id == session_id)
            .order_by(OutgoingMessage.created_at.asc())
        )
        return list(self._session.execute(stmt).scalars())


class IdempotencyKeyRepository(TenantScopedRepository[IdempotencyKey]):
    """Repository for content-based dedup keys with TTL semantics.

    ``record`` is the single public method. It inserts a row with the
    given ``(idempotency_key, customer_phone, body_hash)``. When a row
    with the same ``(tenant_id, idempotency_key)`` already exists and
    has not expired, the method returns the existing row with
    ``is_new=False`` — signalling a duplicate within the dedup window.

    Expired keys are silently overwritten (the old row is deleted and
    a new one inserted).
    """

    model = IdempotencyKey

    def record(
        self,
        *,
        idempotency_key: str,
        customer_phone: str,
        body_hash: str,
        provider_message_id: str | None = None,
        ttl_seconds: int = 30,
    ) -> tuple[IdempotencyKey, bool]:
        """Insert or detect duplicate within the dedup window.

        Returns ``(row, is_new)`` where ``is_new=False`` means the
        same key was already recorded within the TTL window.
        """
        now = _utcnow_naive()

        # Check existing non-expired key.
        stmt = (
            self._by_tenant_stmt()
            .where(IdempotencyKey.idempotency_key == idempotency_key)
        )
        existing = self._session.execute(stmt).scalar_one_or_none()

        if existing is not None:
            if existing.expires_at > now:
                return existing, False
            # Expired — delete and re-insert.
            self._session.delete(existing)
            self._session.flush()

        row = IdempotencyKey(
            id=uuid.uuid4(),
            tenant_id=self._tenant_id,
            idempotency_key=idempotency_key,
            customer_phone=customer_phone,
            body_hash=body_hash,
            provider_message_id=provider_message_id,
            expires_at=now + _dt.timedelta(seconds=ttl_seconds),
        )
        self._session.add(row)
        try:
            self._session.flush()
        except IntegrityError:
            self._session.rollback()
            found = self._session.execute(
                self._by_tenant_stmt().where(
                    IdempotencyKey.idempotency_key == idempotency_key
                )
            ).scalar_one_or_none()
            if found is None:  # pragma: no cover — defensive
                raise
            return found, False
        return row, True

    def prune_expired(self) -> int:
        """Delete all expired keys. Returns the count of deleted rows."""
        stmt = (
                self._by_tenant_stmt().where(IdempotencyKey.expires_at <= _utcnow_naive())
            )
        expired = list(self._session.execute(stmt).scalars())
        for row in expired:
            self._session.delete(row)
        if expired:
            self._session.flush()
        return len(expired)


# ── Dedup window (time-based burst suppression) ───────────────────────────────


class ConversationLockRepository(TenantScopedRepository[ConversationLock]):
    """Per-customer advisory lock for conversation serialization.

    ``acquire`` attempts to create a lock row for ``(tenant, customer_phone)``.
    If the row already exists and has not expired, the lock is held by
    another worker — callers should retry or skip.

    ``release`` deletes the lock row.
    """

    model = ConversationLock

    LOCK_TTL_SECONDS: int = 30  # max time a single message should take to process

    def acquire(
        self,
        customer_phone: str,
        *,
        worker_id: str | None = None,
        ttl_seconds: int | None = None,
        retries: int = 3,
        retry_delay_ms: int = 100,
    ) -> tuple[ConversationLock | None, bool]:
        """Acquire an advisory lock for ``(tenant, customer_phone)``.

        Returns ``(lock, True)`` on success, ``(None, False)`` when the
        lock is held by another worker (or a conflict was not resolved
        after retries).

        The lock expires after ``ttl_seconds``, preventing deadlocks.
        """
        import time as _time

        effective_ttl = ttl_seconds or self.LOCK_TTL_SECONDS
        wid = worker_id or f"worker-{uuid.uuid4().hex[:8]}"

        now = _utcnow_naive()

        for attempt in range(retries):
            # Clean up any expired lock for this customer first.
            stmt = (
                self._by_tenant_stmt()
                .where(ConversationLock.customer_phone == customer_phone)
            )
            existing = self._session.execute(stmt).scalar_one_or_none()

            if existing is not None:
                if existing.expires_at > now and existing.is_active:
                    # Lock still valid — held by another worker.
                    if attempt < retries - 1:
                        self._session.rollback()
                        _time.sleep(retry_delay_ms / 1000.0)
                        continue
                    return None, False
                # Expired or inactive — re-use the row.
                existing.is_active = True
                existing.worker_id = wid
                existing.acquired_at = now  # type: ignore[assignment]
                existing.expires_at = now + _dt.timedelta(seconds=effective_ttl)
                self._session.flush()
                return existing, True

            # No existing lock — create one.
            lock = ConversationLock(
                id=uuid.uuid4(),
                tenant_id=self._tenant_id,
                customer_phone=customer_phone,
                worker_id=wid,
                is_active=True,
                expires_at=now + _dt.timedelta(seconds=effective_ttl),
            )
            self._session.add(lock)
            try:
                self._session.flush()
            except IntegrityError:
                self._session.rollback()
                if attempt < retries - 1:
                    _time.sleep(retry_delay_ms / 1000.0)
                    continue
                return None, False
            return lock, True

        return None, False

    def release(self, customer_phone: str) -> bool:
        """Release the lock for ``customer_phone``.

        Returns ``True`` if a lock was found and released, ``False`` if
        no lock existed.
        """
        stmt = (
            self._by_tenant_stmt()
            .where(ConversationLock.customer_phone == customer_phone)
            .where(ConversationLock.is_active.is_(True))
        )
        lock = self._session.execute(stmt).scalar_one_or_none()
        if lock is None:
            return False
        lock.is_active = False
        self._session.flush()
        return True

    def prune_expired(self) -> int:
        """Delete or deactivate all expired locks. Returns count."""
        stmt = (
            self._by_tenant_stmt().where(
                ConversationLock.expires_at <= _utcnow_naive()
            )
        )
        expired = list(self._session.execute(stmt).scalars())
        for row in expired:
            row.is_active = False
        if expired:
            self._session.flush()
        return len(expired)

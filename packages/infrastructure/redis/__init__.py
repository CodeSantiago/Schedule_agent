"""Redis connection manager with graceful degradation.

Provides a ``RedisManager`` that wraps ``redis-py`` with:

- Lazy connection (no import-time dependency).
- Graceful fallback to ``NullRedis`` when Redis is unavailable or
  not configured — production features degrade gracefully in dev.
- Connection health checks (PING on first use).
- Configurable via environment variables with sensible defaults.

All clients implement a common ``RedisClient`` Protocol so callers
can write against the same surface whether or not Redis is actually
available.

Usage::

    from packages.infrastructure.redis import get_redis

    r = get_redis()
    r.set("key", "value", ex=60)   # graceful no-op when Redis is down
    val = r.get("key")             # returns None when Redis is down
"""

from __future__ import annotations

import os
from typing import Any, Optional, Protocol


# ── Protocol ──────────────────────────────────────────────────────────────────


class RedisClient(Protocol):
    """Minimal Redis-like surface for the application layer.

    Covers the operations this codebase uses: key-value get/set/delete,
    pub/sub for future multiworker signalling, and health checks.
    """

    def get(self, key: str) -> Optional[str]: ...

    def set(
        self,
        key: str,
        value: str,
        *,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool: ...

    def delete(self, key: str) -> bool: ...

    def compare_and_delete(self, key: str, expected_value: str) -> bool: ...

    def exists(self, key: str) -> bool: ...

    def ping(self) -> bool: ...

    def is_available(self) -> bool: ...


# ── Null client (graceful fallback) ───────────────────────────────────────────


class NullRedis:
    """No-op Redis client for when Redis is not configured or unavailable.

    Every operation returns a safe default (``None``, ``False``, etc.)
    so callers do not need to check ``is_available`` before every call.
    """

    def get(self, key: str) -> Optional[str]:
        return None

    def set(
        self,
        key: str,
        value: str,
        *,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool:
        return False

    def delete(self, key: str) -> bool:
        return False

    def compare_and_delete(self, key: str, expected_value: str) -> bool:
        return False

    def exists(self, key: str) -> bool:
        return False

    def ping(self) -> bool:
        return False

    def is_available(self) -> bool:
        return False


# ── Real client (lazy) ────────────────────────────────────────────────────────


class _RealRedis:
    """Wrapper around ``redis.Redis`` with lazy init and health check.

    The underlying client is created on first use, not at construction
    time, so importing this module does not require the ``redis``
    package to be installed.
    """

    def __init__(self, url: str) -> None:
        self._url = url
        self._client: Any = None
        self._available: bool | None = None

    def _ensure(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import redis as _redis

            self._client = _redis.from_url(
                self._url,
                socket_connect_timeout=3,
                socket_timeout=3,
                decode_responses=True,
            )
            self._client.ping()
            self._available = True
        except Exception:
            self._available = False
            self._client = None
        return self._client

    def get(self, key: str) -> Optional[str]:
        client = self._ensure()
        if client is None:
            return None
        try:
            return client.get(key)
        except Exception:
            return None

    def set(
        self,
        key: str,
        value: str,
        *,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool:
        client = self._ensure()
        if client is None:
            return False
        try:
            return bool(client.set(key, value, ex=ex, nx=nx))
        except Exception:
            return False

    def delete(self, key: str) -> bool:
        client = self._ensure()
        if client is None:
            return False
        try:
            return bool(client.delete(key))
        except Exception:
            return False

    def compare_and_delete(self, key: str, expected_value: str) -> bool:
        client = self._ensure()
        if client is None:
            return False
        try:
            current = client.get(key)
            if current != expected_value:
                return False
            return bool(client.delete(key))
        except Exception:
            return False

    def exists(self, key: str) -> bool:
        client = self._ensure()
        if client is None:
            return False
        try:
            return bool(client.exists(key))
        except Exception:
            return False

    def ping(self) -> bool:
        client = self._ensure()
        if client is None:
            return False
        try:
            return client.ping()
        except Exception:
            return False

    def is_available(self) -> bool:
        self._ensure()
        return bool(self._available)


# ── Module-level singleton ────────────────────────────────────────────────────


_REDIS_INSTANCE: RedisClient | None = None


def get_redis() -> RedisClient:
    """Return the module-level Redis client (lazy singleton).

    The client is resolved once and cached. Resolution order:

    1. ``REDIS_URL`` environment variable → real Redis client.
    2. ``REDIS_ENABLED=true`` env var → real Redis from ``REDIS_URL``
       or ``redis://localhost:6379/0`` default.
    3. Everything else → ``NullRedis`` (graceful degradation).
    """
    global _REDIS_INSTANCE
    if _REDIS_INSTANCE is not None:
        return _REDIS_INSTANCE

    url = os.environ.get("REDIS_URL", "").strip()
    if not url:
        if os.environ.get("REDIS_ENABLED", "").lower() in ("true", "1", "yes"):
            url = "redis://localhost:6379/0"
        else:
            _REDIS_INSTANCE = NullRedis()
            return _REDIS_INSTANCE

    try:
        _REDIS_INSTANCE = _RealRedis(url)
        # Trigger the health check on first access so we can fall
        # back to NullRedis immediately.
        if not _REDIS_INSTANCE.is_available():
            _REDIS_INSTANCE = NullRedis()
    except Exception:
        _REDIS_INSTANCE = NullRedis()

    return _REDIS_INSTANCE


def reset_redis() -> None:
    """Reset the singleton (for testing)."""
    global _REDIS_INSTANCE
    _REDIS_INSTANCE = None

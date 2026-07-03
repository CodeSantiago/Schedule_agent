"""Background task queue abstraction.

Provides a ``TaskQueue`` Protocol and two implementations:

1. ``InProcessQueue`` — runs tasks synchronously (default for dev).
2. ``ThreadedQueue`` — runs tasks in a thread pool (lightweight async).

The webhook handler uses this to defer expensive work (transport sends,
LLM calls for non-critical classification, audit logging) to the
background so the webhook response stays fast.

Usage::

    from packages.infrastructure.queue import get_queue

    queue = get_queue()
    queue.enqueue(send_message, to_phone="...", body="...")
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from typing import Any, Protocol


class TaskQueue(Protocol):
    """A queue that accepts tasks for deferred execution."""

    def enqueue(
        self, fn: Callable[..., Any], *args: Any, **kwargs: Any
    ) -> None: ...

    def enqueue_in(
        self,
        delay_seconds: float,
        fn: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> None: ...

    def shutdown(self, wait: bool = True) -> None: ...


class InProcessQueue:
    """Synchronous queue: runs tasks immediately in the calling thread.

    This is the default for development and testing. Tasks are
    executed inline so failures surface immediately and the call
    stack is preserved for debugging.
    """

    def enqueue(
        self, fn: Callable[..., Any], *args: Any, **kwargs: Any
    ) -> None:
        fn(*args, **kwargs)

    def enqueue_in(
        self,
        delay_seconds: float,
        fn: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> None:
        timer = threading.Timer(delay_seconds, fn, args=args, kwargs=kwargs)
        timer.daemon = True
        timer.start()

    def shutdown(self, wait: bool = True) -> None:
        pass


class ThreadedQueue:
    """Lightweight thread-pool queue for production.

    Runs tasks in a fixed thread pool so the caller returns quickly.
    Useful for deferring transport sends, audit writes, and
    non-critical LLM calls.
    """

    def __init__(self, max_workers: int = 4) -> None:
        import concurrent.futures as _futures

        self._executor = _futures.ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="bgq",
        )
        self._lock = threading.Lock()
        self._shutdown = False
        self._timers: list[threading.Timer] = []

    def enqueue(
        self, fn: Callable[..., Any], *args: Any, **kwargs: Any
    ) -> None:
        if self._shutdown:
            return
        self._executor.submit(fn, *args, **kwargs)

    def enqueue_in(
        self,
        delay_seconds: float,
        fn: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> None:
        if self._shutdown:
            return

        def _submit() -> None:
            if self._shutdown:
                return
            self._executor.submit(fn, *args, **kwargs)

        timer = threading.Timer(delay_seconds, _submit)
        timer.daemon = True
        self._timers.append(timer)
        timer.start()

    def shutdown(self, wait: bool = True) -> None:
        with self._lock:
            if self._shutdown:
                return
            self._shutdown = True
            for timer in self._timers:
                try:
                    timer.cancel()
                except Exception:
                    pass
        self._executor.shutdown(wait=wait)


# ── Module-level singleton ────────────────────────────────────────────────────


_QUEUE_INSTANCE: TaskQueue | None = None


def get_queue() -> TaskQueue:
    """Return the module-level task queue (lazy singleton).

    Returns a ``ThreadedQueue`` when ``BACKGROUND_QUEUE=threaded`` or
    ``ENV=production`` is set. Returns ``InProcessQueue`` otherwise.
    """
    global _QUEUE_INSTANCE
    if _QUEUE_INSTANCE is not None:
        return _QUEUE_INSTANCE

    mode = os.environ.get("BACKGROUND_QUEUE", "").strip().lower()
    env = os.environ.get("ENV", "development").strip().lower()

    if mode == "threaded" or (env == "production" and mode != "inprocess"):
        _QUEUE_INSTANCE = ThreadedQueue()
    else:
        _QUEUE_INSTANCE = InProcessQueue()

    return _QUEUE_INSTANCE


def reset_queue() -> None:
    """Reset the singleton (for testing)."""
    global _QUEUE_INSTANCE
    _QUEUE_INSTANCE = None

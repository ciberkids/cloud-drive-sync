"""A fan-out point for daemon events.

Before this, the engine held a *single* notify callback slot
(``SyncEngine.set_notify_callback``), wired in three places to the IPC server's
``notify_all``. Anything else that wanted to observe events had to replace it, which
silently broke the live UI. The bus turns that slot into a list.

Two design points carry their weight and should not be simplified away.

**Subscriber exceptions never propagate.** A consumer is an observer; it must not be
able to affect the thing it observes. This is not theoretical: the ``sync_complete``
emission sits immediately before the change-token upsert and ``_start_continuous``, so
an exception escaping a subscriber used to leave a pair with no watcher and no poller
until the daemon restarted (#59). ``emit`` therefore catches per subscriber, logs once,
and carries on to the next one.

**``emit`` is cheap and does no I/O.** Subscribers get the event name and the params
dict as they are. Anything expensive -- serialising, resolving configuration, signing,
talking to the network -- belongs in the subscriber's own task, not on the caller's
stack. The caller is usually mid-sync-pass and every millisecond spent here is a
millisecond the whole single-threaded daemon is stalled.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from cloud_drive_sync.util.logging import get_logger

log = get_logger("events")

#: An async consumer: ``await callback(event_name, params)``.
Subscriber = Callable[[str, dict], Awaitable[None]]

#: Seconds between repeat log lines for a subscriber that keeps failing. A broken
#: consumer on a high-frequency event would otherwise fill the log with identical
#: tracebacks and bury whatever else was happening.
_FAILURE_LOG_INTERVAL = 60.0


class EventBus:
    """Delivers daemon events to any number of independent subscribers."""

    def __init__(self) -> None:
        self._subscribers: list[Subscriber] = []
        # Keyed by id(subscriber): when we last logged a failure, and how many we
        # have swallowed since. Reported together so a suppressed burst is still
        # visible as a count rather than vanishing.
        self._last_failure_log: dict[int, float] = {}
        self._suppressed: dict[int, int] = {}

    def subscribe(self, callback: Subscriber) -> None:
        """Register a consumer. Duplicate registrations are ignored."""
        if callback not in self._subscribers:
            self._subscribers.append(callback)

    def unsubscribe(self, callback: Subscriber) -> None:
        if callback in self._subscribers:
            self._subscribers.remove(callback)
            self._last_failure_log.pop(id(callback), None)
            self._suppressed.pop(id(callback), None)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    async def emit(self, event: str, params: dict) -> None:
        """Deliver an event to every subscriber.

        Never raises. A subscriber that fails is logged and skipped; the others still
        receive the event, and the caller is unaffected either way.
        """
        # Iterate a copy: a subscriber may unsubscribe itself while being called.
        for subscriber in list(self._subscribers):
            try:
                await subscriber(event, params)
            except Exception:
                self._record_failure(subscriber, event)

    def _record_failure(self, subscriber: Subscriber, event: str) -> None:
        key = id(subscriber)
        now = time.monotonic()
        last = self._last_failure_log.get(key, 0.0)
        if now - last >= _FAILURE_LOG_INTERVAL:
            suppressed = self._suppressed.pop(key, 0)
            extra = f" ({suppressed} similar suppressed)" if suppressed else ""
            log.exception(
                "Event subscriber %r failed handling %r%s",
                getattr(subscriber, "__qualname__", subscriber),
                event,
                extra,
            )
            self._last_failure_log[key] = now
        else:
            self._suppressed[key] = self._suppressed.get(key, 0) + 1

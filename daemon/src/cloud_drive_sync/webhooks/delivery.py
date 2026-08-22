"""Deliver events to targets without ever slowing down a sync.

The governing constraint: a hung endpoint is a normal condition, not an exception. So
nothing on the sync path may await the network, and nothing here may raise into a
caller.

Shape: one bounded channel per ``target_key``, one worker task each. Per-target
ordering holds; targets are independent, so a dead endpoint cannot delay a healthy
one. All expensive work -- serialising, truncating, signing -- happens **in the
worker**, because doing it at the emit site would put unbounded synchronous CPU on the
event loop, and for ``sync.completed`` on a large initial sync that is tens of
thousands of paths, once per target, on the thread that is also running every other
pair's transfers.

Retries are hand-rolled rather than using :mod:`cloud_drive_sync.util.retry`. That
helper takes its parameters at *function definition* time and dispatches on exception
type only: no notion of a retryable HTTP status, no ``Retry-After``, no per-target
``max_attempts``. Bending it here would be worse than writing the loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import random
import time
from collections import deque
from dataclasses import dataclass, field

import aiohttp

from cloud_drive_sync.util.logging import get_logger
from cloud_drive_sync.webhooks.auth import MissingSecret, build_headers, secret_values
from cloud_drive_sync.webhooks.payload import PayloadContext, RawEvent, build_body
from cloud_drive_sync.webhooks.redaction import (
    classify_error,
    describe_failure,
    install_scrubber,
    safe_endpoint,
)
from cloud_drive_sync.webhooks.resolver import ResolvedTarget

log = get_logger("webhooks.delivery")

#: Per-target queue bound. Drop-oldest on overflow: a monitoring receiver wants
#: current state more than history.
DEFAULT_QUEUE_SIZE = 1000

#: Separate, smaller lane for events a human is waiting on. "Never dropped" is the
#: intent, but an unbounded queue is not an option, so this is the honest bound --
#: overflow here is logged at ERROR rather than counted quietly.
PRIORITY_QUEUE_SIZE = 200

#: Statuses worth retrying. Everything else in the 4xx range is a configuration
#: error, and retrying a 401 five times per event turns a typo into a flood.
RETRYABLE_STATUSES = frozenset({408, 429})

CONNECT_TIMEOUT_SECONDS = 5
BASE_BACKOFF_SECONDS = 1.0
MAX_BACKOFF_SECONDS = 60.0

#: Consecutive failures before a target is treated as down.
BREAKER_THRESHOLD = 10
#: While unhealthy, attempt at most one delivery this often.
BREAKER_PROBE_SECONDS = 300.0

#: A retry chain longer than this is abandoned: an event that has been retried for
#: five minutes has outlived its own relevance.
MAX_TOTAL_ELAPSED_SECONDS = 300.0


@dataclass
class TargetStats:
    """What ``webhook status`` reports for one target."""

    target_key: str
    endpoint: str
    delivered: int = 0
    failed: int = 0
    dropped: int = 0
    consecutive_failures: int = 0
    healthy: bool = True
    last_status: int | None = None
    last_error: str = ""
    queued: int = 0

    def as_dict(self) -> dict:
        return {
            "target_key": self.target_key,
            "endpoint": self.endpoint,
            "delivered": self.delivered,
            "failed": self.failed,
            "dropped": self.dropped,
            "healthy": self.healthy,
            "consecutive_failures": self.consecutive_failures,
            "last_status": self.last_status,
            "last_error": self.last_error,
            "queued": self.queued,
        }


@dataclass
class _Outcome:
    """The result of one HTTP attempt, reduced to what the retry loop needs."""

    ok: bool
    status: int | None = None
    reason: str = ""
    retryable: bool = False
    retry_after: float | None = None


@dataclass
class _Item:
    """One event bound for one target, with the scope it was emitted in."""

    raw: RawEvent
    scope: dict
    #: Wall clock when it was queued, for the total-elapsed cap.
    queued_at: float = field(default_factory=time.monotonic)


class _Channel:
    """One target's queues, worker and health."""

    def __init__(self, target: ResolvedTarget, queue_size: int) -> None:
        self.target = target
        self.normal: deque[_Item] = deque()
        self.priority: deque[_Item] = deque()
        self.queue_size = queue_size
        self.stats = TargetStats(
            target_key=target.target_key, endpoint=safe_endpoint(target.url)
        )
        self.wake = asyncio.Event()
        self.task: asyncio.Task | None = None
        self.unhealthy_since: float | None = None

    def enqueue(self, item: _Item) -> None:
        """Never blocks, never raises. Overflow drops the oldest and counts it."""
        lane, cap, priority = (
            (self.priority, PRIORITY_QUEUE_SIZE, True)
            if item.raw.is_priority
            else (self.normal, self.queue_size, False)
        )
        if len(lane) >= cap:
            lane.popleft()
            self.stats.dropped += 1
            if priority:
                # Loud: this lane holds the events someone is waiting for.
                log.error(
                    "Webhook priority queue for %s is full (%d); dropped the oldest "
                    "event. The endpoint has been unreachable long enough to lose "
                    "alerts.",
                    self.stats.target_key,
                    cap,
                )
            elif self.stats.dropped % 100 == 1:
                log.warning(
                    "Webhook queue for %s is full (%d); dropping oldest events "
                    "(%d dropped so far)",
                    self.stats.target_key,
                    cap,
                    self.stats.dropped,
                )
        lane.append(item)
        self.stats.queued = len(self.normal) + len(self.priority)
        self.wake.set()

    def pop(self) -> _Item | None:
        if self.priority:
            item = self.priority.popleft()
        elif self.normal:
            item = self.normal.popleft()
        else:
            return None
        self.stats.queued = len(self.normal) + len(self.priority)
        return item


class WebhookDelivery:
    """Owns the HTTP session, the per-target channels and their workers."""

    def __init__(
        self,
        context: PayloadContext,
        *,
        queue_size: int = DEFAULT_QUEUE_SIZE,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self._context = context
        self._queue_size = queue_size
        self._channels: dict[str, _Channel] = {}
        self._session = session
        self._owns_session = session is None
        self._running = False
        self._scrubber = install_scrubber()

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        if self._session is None:
            # No global timeout on the session: it is set per request from the
            # target's own timeout_seconds.
            self._session = aiohttp.ClientSession()

    async def stop(self) -> None:
        """Stop accepting work and let in-flight requests finish briefly.

        Queued-but-unsent events are lost; the queue is in memory by design (see the
        proposal's phase 4). Shutdown does not block on draining, because a daemon
        that will not exit because a webhook endpoint is slow is a worse problem than
        a lost notification.
        """
        self._running = False
        for channel in self._channels.values():
            channel.wake.set()
        tasks = [c.task for c in self._channels.values() if c.task]
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._channels.clear()
        if self._session is not None and self._owns_session:
            await self._session.close()
            self._session = None

    def stats(self) -> list[dict]:
        return [c.stats.as_dict() for c in self._channels.values()]

    def submit(self, raw: RawEvent, target: ResolvedTarget, scope: dict) -> None:
        """Queue one event for one target. Synchronous, O(1), never raises.

        This is the only method the sync path reaches, which is why it does no
        serialisation and no I/O.
        """
        if not self._running:
            return
        channel = self._channels.get(target.target_key)
        if channel is None:
            channel = _Channel(target, self._queue_size)
            self._channels[target.target_key] = channel
            channel.task = asyncio.create_task(self._worker(channel))
            # Register literal and env-sourced secrets so the log scrubber can catch
            # them if anything ever formats one into a record.
            self._scrubber.add_secrets(secret_values(target))
        else:
            # Configuration may have changed since the channel was created; deliver
            # with the current view. Identity is target_key, so health and queue
            # survive a config edit rather than resetting.
            channel.target = target
        channel.enqueue(_Item(raw=raw, scope=scope))

    async def _worker(self, channel: _Channel) -> None:
        while self._running:
            await channel.wake.wait()
            channel.wake.clear()
            while self._running:
                item = channel.pop()
                if item is None:
                    break
                if channel.unhealthy_since is not None:
                    since = time.monotonic() - channel.unhealthy_since
                    if since < BREAKER_PROBE_SECONDS:
                        # Breaker open: hold the event rather than hammering a dead
                        # endpoint. Put it back and wait for the probe window.
                        if item.raw.is_priority:
                            channel.priority.appendleft(item)
                        else:
                            channel.normal.appendleft(item)
                        await asyncio.sleep(
                            min(BREAKER_PROBE_SECONDS - since, 5.0)
                        )
                        channel.wake.set()
                        break
                with contextlib.suppress(Exception):
                    await self._deliver(channel, item)

    async def _deliver(self, channel: _Channel, item: _Item) -> None:
        target = channel.target
        attempt = 0
        while self._running:
            attempt += 1
            if time.monotonic() - item.queued_at > MAX_TOTAL_ELAPSED_SECONDS:
                log.warning(
                    "Abandoning %s after %.0fs of retries: %s",
                    item.raw.event,
                    MAX_TOTAL_ELAPSED_SECONDS,
                    channel.stats.target_key,
                )
                self._record_failure(channel, None, "retry window exhausted", attempt)
                return

            outcome = await self._attempt(channel, item, attempt)
            if outcome.ok:
                channel.stats.delivered += 1
                channel.stats.consecutive_failures = 0
                channel.stats.last_status = outcome.status
                channel.stats.last_error = ""
                if channel.unhealthy_since is not None:
                    log.info("Webhook target %s recovered", channel.stats.target_key)
                    channel.unhealthy_since = None
                    channel.stats.healthy = True
                return

            self._record_failure(channel, outcome.status, outcome.reason, attempt)
            if not outcome.retryable or attempt >= target.max_attempts:
                return
            await asyncio.sleep(self._backoff(attempt, outcome.retry_after))

    async def _attempt(
        self, channel: _Channel, item: _Item, attempt: int
    ) -> _Outcome:
        target = channel.target
        try:
            body, _envelope = build_body(
                item.raw, target, self._context, attempt=attempt, scope=item.scope
            )
            headers = build_headers(target, body)
        except MissingSecret as exc:
            # Deployment error, not an endpoint error. Not retryable: the variable
            # will not appear mid-run. The message names the variable, never a value.
            return _Outcome(ok=False, reason=str(exc), retryable=False)
        except Exception as exc:
            return _Outcome(ok=False, reason=classify_error(exc), retryable=False)

        assert self._session is not None
        timeout = aiohttp.ClientTimeout(
            total=target.timeout_seconds, connect=CONNECT_TIMEOUT_SECONDS
        )
        try:
            async with self._session.post(
                target.url,
                data=body,
                headers=headers,
                timeout=timeout,
                # A 302 to a link-local address is the standard way to turn an
                # outbound webhook into a request forger, and no legitimate receiver
                # needs a redirect.
                allow_redirects=False,
                # `ssl=False` disables verification; None keeps the default.
                ssl=None if target.verify_tls else False,
            ) as response:
                status = response.status
                retry_after = _parse_retry_after(response.headers.get("Retry-After"))
                # The body is attacker-influenced text. Read and discard it so the
                # connection can be reused, and never log or store it.
                await response.read()
        except Exception as exc:
            # Caught at the boundary. `classify_error` reads only the exception
            # *type*: aiohttp's str() embeds the URL query and its repr() embeds the
            # Authorization header verbatim.
            return _Outcome(
                ok=False, reason=classify_error(exc), retryable=True
            )

        if 200 <= status < 300:
            return _Outcome(ok=True, status=status)

        retryable = status in RETRYABLE_STATUSES or 500 <= status < 600
        return _Outcome(
            ok=False,
            status=status,
            reason="error response",
            retryable=retryable,
            retry_after=retry_after,
        )

    def _record_failure(
        self, channel: _Channel, status: int | None, reason: str, attempt: int
    ) -> None:
        channel.stats.failed += 1
        channel.stats.consecutive_failures += 1
        channel.stats.last_status = status
        channel.stats.last_error = reason
        log.warning(
            "Webhook delivery failed: %s",
            describe_failure(
                target_key=channel.stats.target_key,
                url=channel.target.url,
                attempt=attempt,
                status=status,
                reason=reason,
            ),
        )
        if (
            channel.stats.consecutive_failures >= BREAKER_THRESHOLD
            and channel.unhealthy_since is None
        ):
            channel.unhealthy_since = time.monotonic()
            channel.stats.healthy = False
            log.error(
                "Webhook target %s marked unhealthy after %d consecutive failures; "
                "backing off to one attempt every %.0fs",
                channel.stats.target_key,
                channel.stats.consecutive_failures,
                BREAKER_PROBE_SECONDS,
            )

    @staticmethod
    def _backoff(attempt: int, retry_after: float | None) -> float:
        if retry_after is not None:
            return min(retry_after, MAX_BACKOFF_SECONDS)
        delay = min(BASE_BACKOFF_SECONDS * (2 ** (attempt - 1)), MAX_BACKOFF_SECONDS)
        # Jitter, not cryptography: spreads retries so N targets do not all wake
        # at the same instant after a shared outage.
        return delay * (0.5 + random.random())


def _parse_retry_after(value: str | None) -> float | None:
    """``Retry-After`` as seconds. Only the delta-seconds form is honoured.

    The HTTP-date form is legal but rare from webhook receivers, and mis-parsing a
    date into a huge sleep is worse than falling back to our own backoff.
    """
    if not value:
        return None
    try:
        seconds = float(value.strip())
    except ValueError:
        return None
    return seconds if 0 <= seconds <= MAX_BACKOFF_SECONDS else None

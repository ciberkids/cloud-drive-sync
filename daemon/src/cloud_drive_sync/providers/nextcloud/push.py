"""Nextcloud change detection over ``notify_push`` (issue #56).

WebDAV has no delta API, so ``NextcloudChangePoller`` walks the whole tree
comparing ETags — one PROPFIND *per directory*, every poll interval. That cost,
multiplied across a tree and repeated every 30 seconds, is what made #44, #47 and
#50 damaging rather than merely inefficient.

Nextcloud's `notify_push <https://github.com/nextcloud/notify_push>`_ app — the
one the official desktop client uses — pushes change notifications over a
WebSocket, and since v0.4 reports the specific file IDs that changed. Those are
``oc:fileid`` values, which this codebase already requests and stores as
``RemoteChange.file_id``, so the identifier arrives in the form the data model is
already keyed on.

Two properties of ``notify_push`` shape the design rather than being worked around:

* **It is best-effort.** Upstream is explicit: "updates might happen without a
  notification being sent and a notification can be sent even if no update has
  actually happened." So it cannot replace polling — it makes polling rare. The
  ETag walk is kept as a reconciliation pass on a long interval.
* **It is often not installed.** It needs Redis, a push daemon and ideally a
  reverse proxy. The capabilities endpoint makes support detectable at runtime,
  so this degrades to plain polling instead of failing.

Uses ``aiohttp``, already a dependency for the HTTP server — no new package.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

from cloud_drive_sync.providers.base import CloudChangePoller
from cloud_drive_sync.providers.gdrive.changes import RemoteChange
from cloud_drive_sync.util.logging import get_logger

log = get_logger("providers.nextcloud.push")

# How often to run the ETag walk anyway, even while push is healthy. Covers the
# best-effort gap: a notification that was never sent would otherwise leave the
# two sides inconsistent until something else touched the file.
RECONCILE_INTERVAL_SECONDS = 15 * 60

# Reconnect backoff, capped so a server that is down does not get hammered and a
# server that recovers is picked up promptly.
BACKOFF_START_SECONDS = 2.0
BACKOFF_MAX_SECONDS = 120.0

# A push connection that keeps dropping is worse than useless — it produces churn
# while missing changes. After this many consecutive failures, give up on push for
# this session and let polling carry it.
MAX_CONSECUTIVE_FAILURES = 5

CAPABILITIES_PATH = "/ocs/v2.php/cloud/capabilities"


async def discover_push_endpoint(
    server_url: str,
    username: str,
    password: str,
    *,
    session: Any = None,
    timeout: float = 15.0,
) -> str | None:
    """Return the ``notify_push`` WebSocket URL, or ``None`` if unsupported.

    Discovery doubles as the feature test: an instance without the app simply does
    not advertise the capability, so there is nothing else to probe.
    """
    import aiohttp

    url = f"{server_url.rstrip('/')}{CAPABILITIES_PATH}"
    headers = {"OCS-APIRequest": "true", "Accept": "application/json"}
    auth = aiohttp.BasicAuth(username, password)

    owns_session = session is None
    if owns_session:
        session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout))
    try:
        async with session.get(url, headers=headers, auth=auth) as resp:
            if resp.status != 200:
                log.debug("Capabilities request returned %s; assuming no push", resp.status)
                return None
            payload = await resp.json()
    except Exception as exc:
        log.debug("Could not read Nextcloud capabilities (%s); assuming no push", exc)
        return None
    finally:
        if owns_session:
            await session.close()

    endpoint = (
        payload.get("ocs", {})
        .get("data", {})
        .get("capabilities", {})
        .get("notify_push", {})
        .get("endpoints", {})
        .get("websocket")
    )
    if not endpoint:
        log.info("Nextcloud does not advertise notify_push; using ETag polling")
        return None
    log.info("Nextcloud advertises notify_push at %s", endpoint)
    return endpoint


class NextcloudPushPoller(CloudChangePoller):
    """Change detection that prefers push notifications and falls back to polling.

    Wraps the ETag poller rather than replacing it: push handles the common case
    cheaply, and the wrapped poller still provides the initial snapshot, the
    periodic reconciliation, and the whole behaviour when push is unavailable.
    """

    def __init__(
        self,
        client: Any,
        fallback: CloudChangePoller,
        *,
        reconcile_interval: float = RECONCILE_INTERVAL_SECONDS,
        force_polling: bool = False,
    ) -> None:
        self._client = client
        self._fallback = fallback
        self._reconcile_interval = reconcile_interval
        self._force_polling = force_polling

        self._pending_ids: set[str] = set()
        self._coarse_signal = False
        self._connected = False
        self._gave_up = False
        self._failures = 0
        self._task: asyncio.Task | None = None
        self._endpoint: str | None = None
        # None means "never reconciled", which forces a walk on the first poll —
        # correct after a restart, since notifications sent while down were missed.
        self._last_reconcile: float | None = None

    # ── Introspection, for the UI and for tests ─────────────────────

    @property
    def push_active(self) -> bool:
        """Whether changes are currently arriving by push rather than polling."""
        return self._connected and not self._gave_up and not self._force_polling

    def describe_mechanism(self) -> str:
        if self._force_polling:
            return "polling (forced by configuration)"
        if self._gave_up:
            return "polling (push failed repeatedly)"
        if self._connected:
            return "push (notify_push)"
        if self._endpoint:
            return "polling (push connecting)"
        return "polling (push unavailable)"

    # ── CloudChangePoller ──────────────────────────────────────────

    async def get_start_page_token(self) -> str:
        """Take the initial snapshot and start the push connection."""
        token = await self._fallback.get_start_page_token()
        self._last_reconcile = asyncio.get_running_loop().time()
        await self.start()
        return token

    async def poll_changes(self, page_token: str) -> tuple[list, str]:
        """Return changes, using push when it is healthy.

        When push is working and nothing has changed this costs nothing at all —
        no request reaches the server. That is the entire point: the previous
        implementation issued one PROPFIND per directory on every call.
        """
        now = asyncio.get_running_loop().time()
        due = (
            self._last_reconcile is None
            or (now - self._last_reconcile) >= self._reconcile_interval
        )

        # Without a healthy push connection this is just the old poller.
        if not self.push_active or due or self._coarse_signal:
            reason = (
                "no push" if not self.push_active
                else "reconciliation due" if due
                else "coarse notify_file — changed files unknown"
            )
            log.debug("Full ETag walk for %s (%s)", self._pair_hint(), reason)
            self._coarse_signal = False
            self._pending_ids.clear()
            self._last_reconcile = now
            return await self._fallback.poll_changes(page_token)

        if not self._pending_ids:
            return [], page_token

        ids = sorted(self._pending_ids)
        self._pending_ids.clear()
        changes, unresolved = await self._resolve_ids(ids)

        if unresolved:
            # A file ID that no longer resolves is a deletion, and the ID alone
            # does not tell us the path the engine needs. Rather than guess, fall
            # back to the walk, which derives removals by diffing paths. Deletions
            # are rarer than edits, so paying for one walk is the right trade.
            log.debug(
                "%d pushed id(s) no longer resolve; reconciling to pick up removals",
                len(unresolved),
            )
            self._last_reconcile = now
            return await self._fallback.poll_changes(page_token)

        log.debug("Push reported %d changed file(s) — no directory walk needed", len(changes))
        return changes, page_token

    # ── Push connection ────────────────────────────────────────────

    async def start(self) -> None:
        """Discover support and, if present, connect in the background."""
        if self._task is not None:
            return
        if self._force_polling:
            log.info("notify_push disabled by configuration; using ETag polling")
            return
        self._endpoint = await discover_push_endpoint(
            self._client._server_url,
            self._client._username,
            self._client._app_password,
        )
        if not self._endpoint:
            return
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._connected = False

    async def _run(self) -> None:
        """Maintain the connection, reconnecting with backoff."""
        backoff = BACKOFF_START_SECONDS
        while self._failures < MAX_CONSECUTIVE_FAILURES:
            try:
                await self._connect_once()
                # A clean close still counts as a failure for backoff purposes:
                # a server that keeps closing the socket is not usable.
                self._failures += 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._failures += 1
                log.warning(
                    "notify_push connection failed (%d/%d): %s",
                    self._failures,
                    MAX_CONSECUTIVE_FAILURES,
                    exc,
                )
            finally:
                self._connected = False

            if self._failures >= MAX_CONSECUTIVE_FAILURES:
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX_SECONDS)

        self._gave_up = True
        log.warning(
            "Giving up on notify_push after %d failures; falling back to ETag polling. "
            "Sync still works, it is just more expensive for the server.",
            self._failures,
        )

    async def _connect_once(self) -> None:
        """One connection lifetime: authenticate, listen, dispatch messages."""
        import aiohttp

        async with (
            aiohttp.ClientSession() as session,
            session.ws_connect(self._endpoint, heartbeat=30) as ws,
        ):
            await ws.send_str(self._client._username)
            await ws.send_str(self._client._app_password)

            greeting = await ws.receive_str()
            if greeting.strip() != "authenticated":
                raise RuntimeError(f"notify_push refused authentication: {greeting!r}")

            # Opt into file-ID granularity. Without this the server sends only the
            # coarse notify_file, which tells us something changed but not what —
            # forcing a full walk on every notification and defeating the purpose.
            await ws.send_str("listen notify_file_id")

            self._connected = True
            self._failures = 0
            log.info("notify_push connected — directory walks now happen only for reconciliation")

            async for message in ws:
                if message.type is aiohttp.WSMsgType.TEXT:
                    self._handle_message(message.data)
                elif message.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break

    def _handle_message(self, raw: str) -> None:
        """Interpret one push message.

        Format is an event name optionally followed by a JSON payload, e.g.
        ``notify_file_id [1,2,3]``.
        """
        text = raw.strip()
        if not text:
            return
        event, _, payload = text.partition(" ")

        if event == "notify_file_id":
            try:
                ids = json.loads(payload)
            except (ValueError, TypeError):
                log.debug("Malformed notify_file_id payload %r; treating as coarse", payload)
                self._coarse_signal = True
                return
            if isinstance(ids, list):
                self._pending_ids.update(str(i) for i in ids)
            else:
                self._coarse_signal = True
        elif event == "notify_file":
            # The server could not determine which files changed.
            self._coarse_signal = True
        elif event in ("notify_activity", "notify_notification"):
            pass  # Not file changes; nothing to sync.
        else:
            log.debug("Ignoring unknown notify_push event %r", event)

    # ── Helpers ────────────────────────────────────────────────────

    async def _resolve_ids(self, ids: list[str]) -> tuple[list[RemoteChange], list[str]]:
        """Turn pushed file IDs into changes, reporting any that no longer exist."""
        changes: list[RemoteChange] = []
        unresolved: list[str] = []
        for file_id in ids:
            try:
                node = await asyncio.to_thread(self._node_by_id, file_id)
            except Exception as exc:
                log.debug("Could not resolve pushed file id %s: %s", file_id, exc)
                unresolved.append(file_id)
                continue
            if node is None:
                unresolved.append(file_id)
                continue
            changes.append(self._node_to_change(file_id, node))
        return changes, unresolved

    def _node_by_id(self, file_id: str) -> Any:
        return self._client._nc.files.by_id(int(file_id))

    @staticmethod
    def _node_to_change(file_id: str, node: Any) -> RemoteChange:
        info = getattr(node, "info", None)
        is_dir = bool(getattr(node, "is_dir", False))
        return RemoteChange(
            file_id=file_id,
            file_name=getattr(node, "name", "") or "",
            mime_type=(
                "httpd/unix-directory"
                if is_dir
                else str(getattr(info, "mimetype", "") or "application/octet-stream")
            ),
            # nc-py-api exposes no checksum, so md5 stays empty here exactly as it
            # does in the ETag poller — sync compares by ETag and mtime.
            md5="",
            modified_time=str(getattr(info, "last_modified", "") or ""),
            removed=False,
            trashed=False,
            parents=[],
        )

    def _pair_hint(self) -> str:
        return getattr(self._fallback, "_pair_id", "nextcloud")

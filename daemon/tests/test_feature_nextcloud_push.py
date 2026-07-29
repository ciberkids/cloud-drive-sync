"""Tests for notify_push change detection (#56).

No Nextcloud instance with notify_push is available here, so the protocol is
exercised against fakes shaped like the real thing. What matters is that each
mechanic works, because every one of them fails *silently* if wrong — the poller
would simply fall back to walking the tree, which is what it exists to avoid, and
nothing would look broken.

The message format and event names come from notify_push's DEVELOPING.md:
authenticate by sending username then password, expect ``authenticated``, opt into
IDs with ``listen notify_file_id``, then receive ``notify_file_id [1,2,3]`` with
coarse ``notify_file`` as the fallback.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from cloud_drive_sync.providers.nextcloud.push import (
    MAX_CONSECUTIVE_FAILURES,
    NextcloudPushPoller,
    discover_push_endpoint,
)


class FakeFallback:
    """Stands in for NextcloudChangePoller; records when a full walk happened."""

    def __init__(self) -> None:
        self.walks = 0
        self.snapshots = 0

    async def get_start_page_token(self) -> str:
        self.snapshots += 1
        return json.dumps({"etags": {}})

    async def poll_changes(self, token):
        self.walks += 1
        return [("walked",)], token


class FakeNode:
    def __init__(self, name: str, is_dir: bool = False) -> None:
        self.name = name
        self.is_dir = is_dir
        self.info = type("info", (), {"mimetype": "text/plain", "last_modified": "2026-07-29"})()


class FakeFiles:
    def __init__(self, nodes: dict[int, Any] | None = None) -> None:
        self._nodes = nodes or {}
        self.lookups: list[int] = []

    def by_id(self, file_id: int):
        self.lookups.append(file_id)
        return self._nodes.get(file_id)


class FakeClient:
    def __init__(self, nodes: dict[int, Any] | None = None) -> None:
        self._server_url = "https://cloud.example.com"
        self._username = "alice"
        self._app_password = "app-pw"  # fake credential for a test double
        self._nc = type("nc", (), {"files": FakeFiles(nodes)})()


def _poller(nodes=None, **kwargs) -> tuple[NextcloudPushPoller, FakeFallback]:
    fallback = FakeFallback()
    return NextcloudPushPoller(FakeClient(nodes), fallback, **kwargs), fallback


# ── Capability discovery doubles as the feature test ────────────────


class FakeResponse:
    def __init__(self, status: int, payload: Any) -> None:
        self.status = status
        self._payload = payload

    async def json(self):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    def __init__(self, response) -> None:
        self._response = response
        self.requests: list[str] = []

    def get(self, url, **kwargs):
        self.requests.append(url)
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def _capabilities(websocket: str | None):
    endpoints = {"websocket": websocket} if websocket else {}
    return {"ocs": {"data": {"capabilities": {"notify_push": {"endpoints": endpoints}}}}}


async def test_discovery_returns_the_websocket_url():
    session = FakeSession(FakeResponse(200, _capabilities("wss://cloud.example.com/push/ws")))

    url = await discover_push_endpoint("https://cloud.example.com", "alice", "pw", session=session)

    assert url == "wss://cloud.example.com/push/ws"
    assert session.requests == ["https://cloud.example.com/ocs/v2.php/cloud/capabilities"]


async def test_discovery_returns_none_when_the_app_is_not_installed():
    """Most instances have no notify_push; that must be ordinary, not an error."""
    session = FakeSession(FakeResponse(200, _capabilities(None)))

    assert await discover_push_endpoint("https://x", "a", "p", session=session) is None


async def test_discovery_tolerates_an_http_error():
    session = FakeSession(FakeResponse(401, {}))

    assert await discover_push_endpoint("https://x", "a", "p", session=session) is None


async def test_discovery_tolerates_a_network_failure():
    """An unreachable server must degrade to polling, not raise into startup."""
    session = FakeSession(OSError("connection refused"))

    assert await discover_push_endpoint("https://x", "a", "p", session=session) is None


async def test_discovery_tolerates_an_unexpected_payload_shape():
    session = FakeSession(FakeResponse(200, {"unexpected": True}))

    assert await discover_push_endpoint("https://x", "a", "p", session=session) is None


# ── Message parsing ─────────────────────────────────────────────────


def test_notify_file_id_collects_the_reported_ids():
    poller, _ = _poller()

    poller._handle_message("notify_file_id [1,2,3]")

    assert poller._pending_ids == {"1", "2", "3"}
    assert poller._coarse_signal is False


def test_notify_file_id_accumulates_across_messages():
    poller, _ = _poller()

    poller._handle_message("notify_file_id [1,2]")
    poller._handle_message("notify_file_id [2,7]")

    assert poller._pending_ids == {"1", "2", "7"}


def test_coarse_notify_file_forces_a_walk():
    """Without file IDs there is nothing to resolve, so the tree must be walked."""
    poller, _ = _poller()

    poller._handle_message("notify_file")

    assert poller._coarse_signal is True
    assert poller._pending_ids == set()


def test_a_malformed_payload_degrades_to_a_walk_rather_than_being_dropped():
    """Silently ignoring it would mean losing the change entirely."""
    poller, _ = _poller()

    poller._handle_message("notify_file_id not-json")

    assert poller._coarse_signal is True


def test_non_file_events_are_ignored():
    poller, _ = _poller()

    poller._handle_message("notify_activity")
    poller._handle_message("notify_notification")
    poller._handle_message("something_else")
    poller._handle_message("")

    assert poller._pending_ids == set()
    assert poller._coarse_signal is False


# ── Polling behaviour: the point is avoiding directory walks ────────


async def test_no_walk_when_push_is_healthy_and_nothing_changed():
    """The whole reason this exists: an idle poll must cost nothing."""
    poller, fallback = _poller()
    poller._connected = True
    poller._last_reconcile = asyncio.get_running_loop().time()

    changes, token = await poller.poll_changes("tok")

    assert changes == []
    assert token == "tok"
    assert fallback.walks == 0, "an idle poll walked the tree anyway"


async def test_pushed_ids_are_resolved_without_a_walk():
    poller, fallback = _poller(nodes={5: FakeNode("notes.txt")})
    poller._connected = True
    poller._last_reconcile = asyncio.get_running_loop().time()
    poller._handle_message("notify_file_id [5]")

    changes, _ = await poller.poll_changes("tok")

    assert fallback.walks == 0
    assert len(changes) == 1
    assert changes[0].file_id == "5"
    assert changes[0].file_name == "notes.txt"


async def test_a_directory_push_reports_the_directory_mime_type():
    poller, _ = _poller(nodes={9: FakeNode("Photos", is_dir=True)})
    poller._connected = True
    poller._last_reconcile = asyncio.get_running_loop().time()
    poller._handle_message("notify_file_id [9]")

    changes, _ = await poller.poll_changes("tok")

    assert changes[0].mime_type == "httpd/unix-directory"


async def test_an_unresolvable_id_triggers_a_walk_to_catch_the_deletion():
    """A deleted file's ID no longer resolves, and the ID alone does not give the
    path the engine needs — so fall back rather than guess."""
    poller, fallback = _poller(nodes={})
    poller._connected = True
    poller._last_reconcile = asyncio.get_running_loop().time()
    poller._handle_message("notify_file_id [404]")

    changes, _ = await poller.poll_changes("tok")

    assert fallback.walks == 1
    assert changes == [("walked",)]


async def test_a_coarse_signal_triggers_a_walk_and_is_cleared():
    poller, fallback = _poller()
    poller._connected = True
    poller._last_reconcile = asyncio.get_running_loop().time()
    poller._handle_message("notify_file")

    await poller.poll_changes("tok")
    assert fallback.walks == 1
    assert poller._coarse_signal is False

    await poller.poll_changes("tok")
    assert fallback.walks == 1, "the coarse flag was not cleared and walked again"


async def test_polling_is_used_entirely_when_push_is_not_connected():
    poller, fallback = _poller()
    poller._last_reconcile = asyncio.get_running_loop().time()

    await poller.poll_changes("tok")

    assert fallback.walks == 1


# ── Reconciliation covers the best-effort gap ───────────────────────


async def test_the_first_poll_after_startup_always_reconciles():
    """Notifications sent while the daemon was down were missed, so a restart must
    not trust push until it has walked once."""
    poller, fallback = _poller()
    poller._connected = True
    assert poller._last_reconcile is None

    await poller.poll_changes("tok")

    assert fallback.walks == 1


async def test_reconciliation_runs_on_its_interval_even_while_push_is_healthy():
    """notify_push is explicitly best-effort: a notification may never be sent."""
    poller, fallback = _poller(reconcile_interval=0.05)
    poller._connected = True
    poller._last_reconcile = asyncio.get_running_loop().time()

    await poller.poll_changes("tok")
    assert fallback.walks == 0

    await asyncio.sleep(0.06)
    await poller.poll_changes("tok")

    assert fallback.walks == 1


async def test_reconciliation_discards_stale_pending_ids():
    """A walk supersedes them, so keeping them would cause redundant lookups."""
    poller, _ = _poller(reconcile_interval=0)
    poller._connected = True
    poller._handle_message("notify_file_id [1,2]")

    await poller.poll_changes("tok")

    assert poller._pending_ids == set()


# ── Reporting which mechanism is in use ─────────────────────────────


def test_mechanism_is_reported_for_each_state():
    poller, _ = _poller()
    assert "unavailable" in poller.describe_mechanism()
    assert poller.push_active is False

    poller._endpoint = "wss://x"
    assert "connecting" in poller.describe_mechanism()

    poller._connected = True
    assert poller.describe_mechanism() == "push (notify_push)"
    assert poller.push_active is True

    poller._gave_up = True
    assert "failed repeatedly" in poller.describe_mechanism()
    assert poller.push_active is False, "a given-up poller must not claim push is active"


# ── Startup and shutdown ────────────────────────────────────────────


async def test_start_takes_a_snapshot_and_does_not_connect_without_support(monkeypatch):
    poller, fallback = _poller()
    monkeypatch.setattr(
        "cloud_drive_sync.providers.nextcloud.push.discover_push_endpoint",
        lambda *a, **k: _async_none(),
    )

    token = await poller.get_start_page_token()

    assert fallback.snapshots == 1
    assert json.loads(token) == {"etags": {}}
    assert poller._task is None, "no connection task should exist without support"


async def test_stop_is_safe_when_never_started():
    poller, _ = _poller()

    await poller.stop()  # must not raise

    assert poller.push_active is False


async def test_giving_up_after_repeated_failures_falls_back_permanently(monkeypatch):
    """A connection that keeps dropping produces churn while missing changes, so
    the poller stops trying rather than flapping forever."""
    poller, fallback = _poller()
    poller._endpoint = "wss://x"
    attempts = 0

    async def _always_fails():
        nonlocal attempts
        attempts += 1
        raise OSError("refused")

    monkeypatch.setattr(poller, "_connect_once", _always_fails)
    monkeypatch.setattr(
        "cloud_drive_sync.providers.nextcloud.push.BACKOFF_START_SECONDS", 0.001
    )
    monkeypatch.setattr("cloud_drive_sync.providers.nextcloud.push.BACKOFF_MAX_SECONDS", 0.001)

    await poller._run()

    assert attempts == MAX_CONSECUTIVE_FAILURES
    assert poller._gave_up is True
    assert poller.push_active is False

    # And it still syncs, by polling.
    poller._last_reconcile = asyncio.get_running_loop().time()
    await poller.poll_changes("tok")
    assert fallback.walks == 1


async def _async_none():
    return None


# ── The registry hands out the push-preferring poller ──────────────


def test_the_provider_registry_uses_the_push_poller():
    pytest.importorskip("nc_py_api", reason="nextcloud extra not installed")
    import cloud_drive_sync.providers.nextcloud  # noqa: F401
    from cloud_drive_sync.providers.registry import get

    poller = get("nextcloud").poller_cls(FakeClient())

    assert isinstance(poller, NextcloudPushPoller)


# ── The handshake itself ────────────────────────────────────────────
#
# The riskiest untested part: get the order or the wording wrong and the server
# simply never sends file IDs, the poller silently walks the tree forever, and
# nothing looks broken. So drive _connect_once against a fake socket.


class FakeWS:
    """A notify_push server, as far as the handshake is concerned."""

    def __init__(self, greeting: str = "authenticated", messages: list[str] | None = None) -> None:
        self.sent: list[str] = []
        self._greeting = greeting
        self._messages = list(messages or [])

    async def send_str(self, text: str) -> None:
        self.sent.append(text)

    async def receive_str(self) -> str:
        return self._greeting

    def __aiter__(self):
        return self

    async def __anext__(self):
        import aiohttp

        if not self._messages:
            raise StopAsyncIteration
        data = self._messages.pop(0)
        return type("msg", (), {"type": aiohttp.WSMsgType.TEXT, "data": data})()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeWSSession:
    def __init__(self, ws: FakeWS) -> None:
        self._ws = ws
        self.connect_kwargs: dict = {}

    def ws_connect(self, url, **kwargs):
        self.connect_kwargs = kwargs
        return self._ws

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _patch_session(monkeypatch, session):
    import aiohttp

    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **k: session)


async def test_handshake_sends_credentials_then_opts_into_file_ids(monkeypatch):
    """Order matters: username, then password, then the listen opt-in.

    Without `listen notify_file_id` the server only ever sends coarse notify_file,
    which forces a full walk on every notification — defeating the feature while
    appearing to work.
    """
    ws = FakeWS(messages=["notify_file_id [11,12]"])
    _patch_session(monkeypatch, FakeWSSession(ws))
    poller, _ = _poller()
    poller._endpoint = "wss://cloud.example.com/push/ws"

    await poller._connect_once()

    assert ws.sent == ["alice", "app-pw", "listen notify_file_id"]
    assert poller._pending_ids == {"11", "12"}


async def test_handshake_refuses_a_bad_greeting(monkeypatch):
    """Anything other than `authenticated` means the credentials were rejected."""
    ws = FakeWS(greeting="err: invalid credentials")
    _patch_session(monkeypatch, FakeWSSession(ws))
    poller, _ = _poller()
    poller._endpoint = "wss://x"

    with pytest.raises(RuntimeError, match="refused authentication"):
        await poller._connect_once()

    assert poller.push_active is False
    # It must not have gone on to subscribe after a failed auth.
    assert "listen notify_file_id" not in ws.sent


async def test_a_successful_connection_resets_the_failure_count(monkeypatch):
    """Otherwise a flaky link would eventually exhaust the budget and give up even
    though it keeps recovering."""
    ws = FakeWS(messages=[])
    _patch_session(monkeypatch, FakeWSSession(ws))
    poller, _ = _poller()
    poller._endpoint = "wss://x"
    poller._failures = MAX_CONSECUTIVE_FAILURES - 1

    await poller._connect_once()

    assert poller._failures == 0


async def test_the_connection_requests_a_heartbeat(monkeypatch):
    """A silently dead socket would look connected while delivering nothing."""
    session = FakeWSSession(FakeWS())
    _patch_session(monkeypatch, session)
    poller, _ = _poller()
    poller._endpoint = "wss://x"

    await poller._connect_once()

    assert session.connect_kwargs.get("heartbeat"), "no heartbeat — a dead socket looks alive"


async def test_messages_received_over_the_socket_reach_the_next_poll(monkeypatch):
    """End-to-end for the mechanism: socket → pending ids → changes, no walk."""
    ws = FakeWS(messages=["notify_file_id [42]"])
    _patch_session(monkeypatch, FakeWSSession(ws))
    poller, fallback = _poller(nodes={42: FakeNode("report.pdf")})
    poller._endpoint = "wss://x"

    await poller._connect_once()
    poller._connected = True
    poller._last_reconcile = asyncio.get_running_loop().time()
    changes, _ = await poller.poll_changes("tok")

    assert fallback.walks == 0
    assert [c.file_name for c in changes] == ["report.pdf"]


# ── The force-polling escape hatch ──────────────────────────────────


async def test_force_polling_skips_discovery_entirely(monkeypatch):
    """It must not even ask the server, so the setting also works as a way to
    avoid the capabilities request on a fragile instance."""
    called = False

    async def _spy(*a, **k):
        nonlocal called
        called = True
        return "wss://x"

    monkeypatch.setattr("cloud_drive_sync.providers.nextcloud.push.discover_push_endpoint", _spy)
    poller, _ = _poller(force_polling=True)

    await poller.start()

    assert called is False
    assert poller._task is None
    assert poller.push_active is False
    assert "forced by configuration" in poller.describe_mechanism()


async def test_force_polling_wins_even_if_a_connection_somehow_exists():
    """Defensive: the flag is the user's explicit instruction and must dominate."""
    poller, fallback = _poller(force_polling=True)
    poller._connected = True

    poller._last_reconcile = asyncio.get_running_loop().time()
    await poller.poll_changes("tok")

    assert fallback.walks == 1, "forced polling still used push"


def test_the_factory_passes_force_polling_through():
    pytest.importorskip("nc_py_api", reason="nextcloud extra not installed")
    from cloud_drive_sync.providers.nextcloud import make_change_poller

    assert make_change_poller(FakeClient(), force_polling=True)._force_polling is True
    assert make_change_poller(FakeClient())._force_polling is False

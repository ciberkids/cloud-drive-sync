"""Integration test: notify_push against a real Nextcloud with the app installed.

Run with:
    pytest tests/test_integration_notify_push.py -m nextcloud -v

The unit tests in test_feature_nextcloud_push.py drive fakes built from
notify_push's DEVELOPING.md. That validates our handling of the protocol *as
documented*, which is not the same as the protocol *as implemented* — and the
failure mode if we got a detail wrong is silent: the poller falls back to walking
the tree, sync keeps working, and nobody notices the feature does nothing.

So this stands up the real thing — Nextcloud, Redis, the notify_push app and its
push daemon — and checks the four assumptions that cannot be verified any other
way:

1. the capabilities endpoint exposes the websocket URL where we look for it
2. the handshake (username, password, expect ``authenticated``) is accepted
3. ``listen notify_file_id`` is accepted, rather than only the coarse event
4. a real file change produces a notification carrying real file IDs

Requires a container runtime and several minutes. Skipped automatically without
one, and never part of the default run.
"""

from __future__ import annotations

import json
import subprocess
import time
import urllib.request
import uuid
from collections.abc import Iterator

import pytest

pytestmark = pytest.mark.nextcloud

_NET = "cds-np-test-net"
_NC = "cds-np-test-nc"
_REDIS = "cds-np-test-redis"
_PROXY = "cds-np-test-proxy"
_NC_IMAGE = "docker.io/library/nextcloud:30-apache"
_REDIS_IMAGE = "docker.io/library/redis:7-alpine"
_SOCAT_IMAGE = "docker.io/alpine/socat"
_ADMIN = "admin"
_PASS = "testpassword123"  # test-only credential for a throwaway container
_HTTP_PORT = 18091
_PUSH_PORT = 7877


def _runtime() -> str | None:
    import shutil

    for cmd in ("podman", "docker"):
        if shutil.which(cmd):
            return cmd
    return None


def _run(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(list(args), capture_output=True, text=True, check=check)


def _occ(rt: str, *args: str) -> subprocess.CompletedProcess:
    return _run(rt, "exec", "-u", "www-data", _NC, "php", "occ", *args)


def _teardown(rt: str) -> None:
    for name in (_PROXY, _NC, _REDIS):
        _run(rt, "rm", "-f", name)
    _run(rt, "network", "rm", "-f", _NET)


@pytest.fixture(scope="module")
def push_server() -> Iterator[tuple[str, str, str]]:
    """Yield ``(base_url, user, password)`` for a Nextcloud with notify_push live."""
    rt = _runtime()
    if rt is None:
        pytest.skip("No container runtime (docker/podman) on PATH")

    _teardown(rt)
    _run(rt, "network", "create", _NET)

    if _run(rt, "run", "-d", "--name", _REDIS, "--network", _NET, _REDIS_IMAGE).returncode:
        _teardown(rt)
        pytest.skip("Could not start Redis")

    started = _run(
        rt, "run", "-d", "--name", _NC, "--network", _NET,
        "-p", f"{_HTTP_PORT}:80",
        "-e", f"NEXTCLOUD_ADMIN_USER={_ADMIN}",
        "-e", f"NEXTCLOUD_ADMIN_PASSWORD={_PASS}",
        _NC_IMAGE,
    )
    if started.returncode:
        _teardown(rt)
        pytest.skip(f"Could not start Nextcloud: {started.stderr.strip()}")

    url = f"http://localhost:{_HTTP_PORT}"

    # Wait for the web server, then install explicitly. The image only
    # auto-installs when a database is configured, so relying on the admin env
    # vars alone leaves it sitting at "installed": false indefinitely.
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/status.php", timeout=5):
                break
        except Exception:
            time.sleep(3)
    else:
        _teardown(rt)
        pytest.skip("Nextcloud web server never came up")

    if _occ(rt, "maintenance:install", "--database", "sqlite",
            "--admin-user", _ADMIN, "--admin-pass", _PASS).returncode:
        _teardown(rt)
        pytest.skip("occ maintenance:install failed")

    _occ(rt, "config:system:set", "trusted_domains", "1", "--value", f"localhost:{_HTTP_PORT}")
    # notify_push requires Redis; without it the app refuses to run.
    _occ(rt, "config:system:set", "redis", "host", "--value", _REDIS)
    _occ(rt, "config:system:set", "redis", "port", "--value", "6379", "--type=integer")
    _occ(rt, "config:system:set", "memcache.distributed", "--value", r"\OC\Memcache\Redis")
    _occ(rt, "config:system:set", "memcache.locking", "--value", r"\OC\Memcache\Redis")
    # The push daemon talks to Nextcloud over loopback and must be trusted as a
    # proxy, or its self-test fails and the capability is never published.
    _occ(rt, "config:system:set", "trusted_proxies", "0", "--value", "::1")
    _occ(rt, "config:system:set", "trusted_proxies", "1", "--value", "127.0.0.1")

    if _occ(rt, "app:install", "notify_push").returncode:
        _teardown(rt)
        pytest.skip("Could not install the notify_push app")

    _run(rt, "exec", "-d", "-u", "www-data", "-e", f"PORT={_PUSH_PORT}", _NC,
         "/var/www/html/custom_apps/notify_push/bin/x86_64/notify_push",
         "/var/www/html/config/config.php")
    time.sleep(6)

    setup = _occ(rt, "notify_push:setup", f"http://localhost:{_PUSH_PORT}")
    if "configuration saved" not in setup.stdout:
        _teardown(rt)
        pytest.skip(f"notify_push setup did not complete:\n{setup.stdout}\n{setup.stderr}")

    # Rootless podman does not expose container IPs to the host, so publish the
    # push port through a sidecar rather than rebuilding the whole stack with it.
    _run(rt, "run", "-d", "--name", _PROXY, "--network", _NET,
         "-p", f"{_PUSH_PORT}:{_PUSH_PORT}", _SOCAT_IMAGE,
         f"tcp-listen:{_PUSH_PORT},fork,reuseaddr", f"tcp-connect:{_NC}:{_PUSH_PORT}")
    time.sleep(4)

    yield url, _ADMIN, _PASS

    _teardown(rt)


class _Client:
    """The attributes NextcloudPushPoller reads off NextcloudClient."""

    def __init__(self, url: str, user: str, password: str) -> None:
        self._server_url = url
        self._username = user
        self._app_password = password
        self._nc = None


class _NoFallback:
    """Fails loudly if the poller falls back, instead of quietly walking."""

    async def get_start_page_token(self) -> str:
        return "{}"

    async def poll_changes(self, token):
        raise AssertionError("poller fell back to the ETag walk")


async def test_capabilities_advertises_the_websocket_endpoint(push_server):
    """Assumption 1: the URL is where we look for it.

    Read from ocs.data.capabilities.notify_push.endpoints.websocket — if upstream
    ever moves it, discovery returns None and we silently poll forever.
    """
    from cloud_drive_sync.providers.nextcloud.push import discover_push_endpoint

    url, user, password = push_server

    endpoint = await discover_push_endpoint(url, user, password)

    assert endpoint, "notify_push is installed but discovery found no endpoint"
    assert endpoint.startswith(("ws://", "wss://")), endpoint


async def test_the_handshake_is_accepted_by_the_real_server(push_server):
    """Assumptions 2 and 3: credentials in the right order, and the opt-in works."""
    import asyncio

    from cloud_drive_sync.providers.nextcloud.push import (
        NextcloudPushPoller,
        discover_push_endpoint,
    )

    url, user, password = push_server
    poller = NextcloudPushPoller(_Client(url, user, password), _NoFallback())
    poller._endpoint = await discover_push_endpoint(url, user, password)

    task = asyncio.create_task(poller._connect_once())
    try:
        for _ in range(80):
            await asyncio.sleep(0.25)
            if poller._connected:
                break
        assert poller._connected, "the real server rejected our handshake"
    finally:
        task.cancel()


async def test_a_real_change_produces_real_file_ids(push_server):
    """Assumption 4, and the one that matters most.

    A coarse ``notify_file`` would still "work" while forcing a full walk on every
    change — the feature would appear functional and deliver nothing. So assert
    that actual file IDs arrive.
    """
    import asyncio

    from cloud_drive_sync.providers.nextcloud.push import (
        NextcloudPushPoller,
        discover_push_endpoint,
    )

    url, user, password = push_server
    poller = NextcloudPushPoller(_Client(url, user, password), _NoFallback())
    poller._endpoint = await discover_push_endpoint(url, user, password)

    task = asyncio.create_task(poller._connect_once())
    try:
        for _ in range(80):
            await asyncio.sleep(0.25)
            if poller._connected:
                break
        assert poller._connected

        name = f"pushtest-{uuid.uuid4().hex[:8]}.txt"
        upload = subprocess.run(
            ["curl", "-s", "-u", f"{user}:{password}", "-T", "/etc/hostname",
             f"{url}/remote.php/dav/files/{user}/{name}"],
            capture_output=True, text=True, check=False,
        )
        assert upload.returncode == 0, upload.stderr

        for _ in range(120):
            await asyncio.sleep(0.25)
            if poller._pending_ids or poller._coarse_signal:
                break

        assert poller._pending_ids or poller._coarse_signal, (
            "a real upload produced no notification at all"
        )
        assert poller._pending_ids, (
            "only the coarse notify_file arrived — `listen notify_file_id` was not "
            "honoured, so every change would force a full tree walk"
        )
        assert all(i.isdigit() for i in poller._pending_ids), poller._pending_ids
    finally:
        task.cancel()


def test_the_fixture_actually_configured_push(push_server):
    """Guards the rig itself: if setup silently half-failed, the assertions above
    would be testing nothing."""
    url, user, password = push_server
    req = urllib.request.Request(
        f"{url}/ocs/v2.php/cloud/capabilities",
        headers={"OCS-APIRequest": "true", "Accept": "application/json"},
    )
    import base64

    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    req.add_header("Authorization", f"Basic {token}")
    with urllib.request.urlopen(req, timeout=15) as resp:
        payload = json.load(resp)

    caps = payload["ocs"]["data"]["capabilities"]
    assert "notify_push" in caps, "the app is not advertising itself; the rig is broken"

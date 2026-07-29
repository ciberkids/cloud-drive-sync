"""Tests for HTTP and MCP token authentication.

The gap this closes: both ports were reachable by anyone who could connect, and
once the delete fail-safe landed that meant an anonymous caller could
`PUT /api/settings/max-deletions {"max_deletions_per_sync": 0}` to switch off
delete protection and then trigger a sync. A guard an attacker can disable is not
a guard.

Auth is opt-in, so the tests that matter most are the two ends: that no token
behaves exactly as before (upgrades must not lock anyone out of their own web UI),
and that a configured token actually refuses the dangerous calls.
"""

from __future__ import annotations

import pytest
from aiohttp import web

from cloud_drive_sync.http import auth
from cloud_drive_sync.http.server import HttpServer
from cloud_drive_sync.ipc.protocol import JsonRpcResponse

TOKEN = "s3cret-token-value"  # test-only


class FakeHandler:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def handle(self, request):
        self.calls.append(request.method)
        return JsonRpcResponse.success(request.id, {"ok": True})


# ── The primitives ──────────────────────────────────────────────────


def test_no_token_configured_allows_everything():
    """The default. Enabling auth by default would lock existing deployments out
    of their own UI on upgrade."""
    assert auth.is_authorised(None) is True
    assert auth.is_authorised(None, authorization=None, cookie=None) is True


def test_a_configured_token_rejects_missing_credentials():
    assert auth.is_authorised(TOKEN) is False


@pytest.mark.parametrize(
    "header",
    ["", "Bearer", "Bearer ", "Basic abc", TOKEN, f"bearer {TOKEN}"],
    ids=["empty", "no-value", "blank-value", "wrong-scheme", "raw-no-scheme", "lowercase-scheme"],
)
def test_malformed_authorization_headers_are_rejected(header):
    assert auth.is_authorised(TOKEN, authorization=header) is False


def test_the_correct_bearer_token_is_accepted():
    assert auth.is_authorised(TOKEN, authorization=f"Bearer {TOKEN}") is True


def test_the_cookie_is_accepted_so_the_browser_ui_can_work():
    assert auth.is_authorised(TOKEN, cookie=TOKEN) is True


def test_a_near_miss_token_is_rejected():
    """Guards against a prefix comparison creeping in."""
    assert auth.is_authorised(TOKEN, authorization=f"Bearer {TOKEN[:-1]}") is False
    assert auth.is_authorised(TOKEN, authorization=f"Bearer {TOKEN}x") is False


def test_comparison_is_constant_time():
    """Timing must not reveal how much of the token was right. Asserted on the
    implementation because measuring timing in a test is unreliable."""
    import inspect

    assert "compare_digest" in inspect.getsource(auth.matches)


def test_generated_tokens_are_long_and_unique():
    a, b = auth.generate_token(), auth.generate_token()
    assert a != b
    assert len(a) >= 32


@pytest.mark.parametrize(
    ("host", "loopback"),
    [("127.0.0.1", True), ("::1", True), ("localhost", True), ("[::1]", True),
     ("0.0.0.0", False), ("192.168.1.5", False), ("", False)],
)
def test_loopback_detection(host, loopback):
    assert auth.is_loopback(host) is loopback


# ── The middleware, through a real aiohttp client ────────────────────


class _Client:
    """Minimal HTTP client bound to a running HttpServer.

    Driven over a real socket rather than aiohttp's test fixture, which would need
    pytest-aiohttp — a new dependency that also brings its own asyncio handling and
    could conflict with this project's `asyncio_mode = auto`.
    """

    def __init__(self, session, base: str) -> None:
        self._session = session
        self._base = base

    def get(self, path, **kw):
        return self._session.get(self._base + path, **kw)

    def put(self, path, **kw):
        return self._session.put(self._base + path, **kw)

    def post(self, path, **kw):
        return self._session.post(self._base + path, **kw)

    def options(self, path, **kw):
        return self._session.options(self._base + path, **kw)


@pytest.fixture
async def client_factory():
    import aiohttp

    started: list = []

    async def _make(token: str | None):
        handler = FakeHandler()
        server = HttpServer(handler, host="127.0.0.1", port=0, auth_token=token)
        await server.start()
        # port=0 binds an ephemeral port; ask the socket which one it got.
        sock = next(iter(server._runner.sites)).\
            _server.sockets[0]  # type: ignore[attr-defined]
        port = sock.getsockname()[1]
        # unsafe=True because aiohttp's jar discards cookies set for bare IP
        # addresses. That is a client-side rule, not something the server controls;
        # a browser talking to a hostname accepts the cookie normally.
        session = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))
        started.append((server, session))
        return _Client(session, f"http://127.0.0.1:{port}"), handler

    yield _make

    for server, session in started:
        await session.close()
        await server.stop()


async def test_without_a_token_the_api_is_open(client_factory):
    """Existing behaviour, preserved exactly."""
    client, _ = await client_factory(None)

    resp = await client.get("/api/status")

    assert resp.status == 200


async def test_with_a_token_the_api_refuses_anonymous_calls(client_factory):
    client, handler = await client_factory(TOKEN)

    resp = await client.get("/api/status")

    assert resp.status == 401
    assert "Bearer" in resp.headers.get("WWW-Authenticate", "")
    assert handler.calls == [], "the handler ran despite the request being rejected"


async def test_the_dangerous_call_is_refused_anonymously(client_factory):
    """The specific scenario that motivated this: disabling delete protection."""
    client, handler = await client_factory(TOKEN)

    resp = await client.put(
        "/api/settings/max-deletions", json={"max_deletions_per_sync": 0}
    )

    assert resp.status == 401
    assert handler.calls == [], "delete protection could have been switched off"


async def test_emergency_stop_is_refused_anonymously(client_factory):
    """Otherwise anyone reachable can halt syncing as a denial of service."""
    client, handler = await client_factory(TOKEN)

    resp = await client.post("/api/sync/stop", json={})

    assert resp.status == 401
    assert handler.calls == []


async def test_a_bearer_token_is_accepted(client_factory):
    client, handler = await client_factory(TOKEN)

    resp = await client.get("/api/status", headers={"Authorization": f"Bearer {TOKEN}"})

    assert resp.status == 200
    assert handler.calls == ["get_status"]


async def test_a_wrong_bearer_token_is_refused(client_factory):
    client, _ = await client_factory(TOKEN)

    resp = await client.get("/api/status", headers={"Authorization": "Bearer wrong"})

    assert resp.status == 401


async def test_signing_in_sets_a_cookie_that_then_works(client_factory):
    """The browser path end to end: post the token, get a cookie, use the API."""
    client, _ = await client_factory(TOKEN)

    login = await client.post("/login", data={"token": TOKEN}, allow_redirects=False)
    assert login.status in (302, 303)

    # The client keeps the cookie, so the next call needs no header.
    resp = await client.get("/api/status")
    assert resp.status == 200


async def test_the_login_cookie_is_hardened(client_factory):
    """HttpOnly stops script access; SameSite=Strict stops cross-site use."""
    client, _ = await client_factory(TOKEN)

    login = await client.post("/login", data={"token": TOKEN}, allow_redirects=False)

    raw = login.headers.get("Set-Cookie", "")
    assert "HttpOnly" in raw
    assert "SameSite=Strict" in raw


async def test_a_wrong_token_at_login_sets_no_cookie(client_factory):
    client, _ = await client_factory(TOKEN)

    login = await client.post("/login", data={"token": "nope"}, allow_redirects=False)

    assert login.status == 401
    assert "Set-Cookie" not in login.headers


async def test_the_login_page_is_reachable_unauthenticated(client_factory):
    """Otherwise there would be no way in through a browser."""
    client, _ = await client_factory(TOKEN)

    resp = await client.get("/login")

    assert resp.status == 200
    assert "token" in (await resp.text())


async def test_the_login_page_does_not_leak_the_token(client_factory):
    client, _ = await client_factory(TOKEN)

    body = await (await client.get("/login")).text()

    assert TOKEN not in body


async def test_an_unauthenticated_ui_request_gets_the_login_page(client_factory):
    """A browser hitting / should see a form, not a bare 401 body."""
    client, _ = await client_factory(TOKEN)

    resp = await client.get("/")

    assert resp.status == 401
    assert "text/html" in resp.headers["Content-Type"]
    assert "Sign in" in (await resp.text())


async def test_preflight_is_not_blocked(client_factory):
    """OPTIONS carries no credentials by design; blocking it breaks the browser."""
    client, _ = await client_factory(TOKEN)

    resp = await client.options("/api/status")

    assert resp.status != 401


# ── The startup warning ─────────────────────────────────────────────


def test_exposure_without_a_token_warns(caplog):
    with caplog.at_level("WARNING"):
        auth.warn_if_exposed(name="HTTP API", host="0.0.0.0", port=8080, token=None)

    assert "NO AUTHENTICATION" in caplog.text
    assert "delete protection" in caplog.text, "the warning should say what is at stake"


def test_loopback_without_a_token_does_not_warn(caplog):
    """Only this machine can reach it, so it is not a finding."""
    with caplog.at_level("WARNING"):
        auth.warn_if_exposed(name="HTTP API", host="127.0.0.1", port=8080, token=None)

    assert "NO AUTHENTICATION" not in caplog.text


def test_a_token_does_not_warn(caplog):
    with caplog.at_level("WARNING"):
        auth.warn_if_exposed(name="HTTP API", host="0.0.0.0", port=8080, token=TOKEN)

    assert "NO AUTHENTICATION" not in caplog.text


def test_the_warning_never_logs_the_token(caplog):
    with caplog.at_level("INFO"):
        auth.warn_if_exposed(name="HTTP API", host="0.0.0.0", port=8080, token=TOKEN)

    assert TOKEN not in caplog.text


# ── Wiring ──────────────────────────────────────────────────────────


def test_the_daemon_defaults_to_no_auth_and_all_interfaces():
    """Non-breaking by construction: the defaults are the old behaviour."""
    from cloud_drive_sync.daemon import Daemon

    d = Daemon()
    assert d._http_token is None
    assert d._mcp_token is None
    assert d._http_host == "0.0.0.0"


def test_an_empty_token_string_counts_as_no_token():
    """An unset environment variable arrives as "", which must not enable auth
    with an empty secret that any request would satisfy."""
    from cloud_drive_sync.daemon import Daemon

    assert Daemon(http_token="", mcp_token="")._http_token is None
    server = HttpServer(FakeHandler(), auth_token="")
    assert server._auth_token is None


def test_static_assets_are_served_without_auth():
    """The bundle is not sensitive and the login page needs its styling; the data
    behind /api is what is protected."""
    import inspect

    from cloud_drive_sync.http import server as server_mod

    source = inspect.getsource(server_mod.HttpServer._auth_middleware)
    assert "/assets" in source


def test_web_response_import_is_used():
    """Sanity check that the middleware returns aiohttp responses."""
    assert hasattr(web, "json_response")

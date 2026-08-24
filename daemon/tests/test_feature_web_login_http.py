"""The web UI sign-in endpoints, driven over a real socket.

The primitives are covered in ``test_feature_web_login.py``; this is about the
five behaviours only the HTTP layer can get wrong: which credential is accepted
in which mode, that the cookie is hardened, that a cookie-authenticated mutation
cannot be forged cross-site, that a bearer-token caller is left alone by those
rules, and — the decision most easily undone by a later refactor — that the
access token stops working as a *browser* credential once an account exists.

See docs/Proposal-Web-UI-Login.md.
"""

from __future__ import annotations

import pytest

from cloud_drive_sync.http import auth
from cloud_drive_sync.http.server import HttpServer
from cloud_drive_sync.ipc.protocol import JsonRpcResponse

TOKEN = "s3cret-token-value"  # test-only
PASSWORD = "correct-horse-battery"  # test-only
USERNAME = "matteo"


class AccountHandler:
    """A handler with a real account, hashed with the real primitives.

    Deliberately not a mock that returns ``ok`` — the point of these tests is that
    a wrong password fails, and a fake that cannot fail would hide it.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.account: dict | None = None

    def install(self, username: str = USERNAME, password: str = PASSWORD) -> None:
        self.account = {
            "username": username,
            "password": auth.hash_password(password),
            "created_at": "2026-01-01T00:00:00+00:00",
            "password_changed_at": "2026-01-01T00:00:00+00:00",
        }

    async def handle(self, request):
        self.calls.append(request.method)
        params = request.params or {}
        result: object = {"ok": True}

        if request.method == "get_web_account":
            result = (
                {"exists": False, "username": None}
                if self.account is None
                else {
                    "exists": True,
                    "username": self.account["username"],
                    "created_at": self.account["created_at"],
                    "password_changed_at": self.account["password_changed_at"],
                }
            )
        elif request.method == "set_web_account":
            username = str(params.get("username", "")).strip()
            password = str(params.get("password", ""))
            problem = auth.username_problem(username) or auth.password_problem(
                password, username=username
            )
            if problem:
                result = {"status": "invalid", "error": problem}
            else:
                self.install(username, password)
                result = {"status": "ok", "username": username}
        elif request.method == "verify_web_account":
            ok = self.account is not None and str(
                params.get("username", "")
            ).strip().lower() == self.account["username"].lower() and auth.verify_password(
                self.account["password"], str(params.get("password", ""))
            )
            result = {"ok": ok, "username": self.account["username"] if ok else None}
        elif request.method == "change_web_password":
            if self.account is None:
                result = {"status": "not_found"}
            elif not auth.verify_password(
                self.account["password"], str(params.get("current", ""))
            ):
                result = {"status": "invalid_credentials"}
            else:
                problem = auth.password_problem(
                    str(params.get("new", "")), username=self.account["username"]
                )
                if problem:
                    result = {"status": "invalid", "error": problem}
                else:
                    self.install(self.account["username"], str(params.get("new")))
                    result = {"status": "ok"}

        return JsonRpcResponse.success(request.id, result)


class _Client:
    def __init__(self, session, base: str) -> None:
        self._session = session
        self._base = base

    def get(self, path, **kw):
        return self._session.get(self._base + path, **kw)

    def put(self, path, **kw):
        return self._session.put(self._base + path, **kw)

    def post(self, path, **kw):
        return self._session.post(self._base + path, **kw)


@pytest.fixture
async def server_factory():
    import aiohttp

    started: list = []

    async def _make(token: str | None = TOKEN, *, account: bool = False, trust_proxy=False):
        handler = AccountHandler()
        if account:
            handler.install()
        server = HttpServer(
            handler, host="127.0.0.1", port=0, auth_token=token, trust_proxy=trust_proxy
        )
        await server.start()
        sock = next(iter(server._runner.sites))._server.sockets[0]  # type: ignore[attr-defined]
        port = sock.getsockname()[1]
        # unsafe=True because aiohttp's jar discards cookies set for bare IP
        # addresses; a browser talking to a hostname accepts them normally.
        session = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))
        started.append((server, session))
        return _Client(session, f"http://127.0.0.1:{port}"), handler, server

    yield _make

    for server, session in started:
        await session.close()
        await server.stop()


async def _sign_in(client, password: str = PASSWORD, username: str = USERNAME):
    return await client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )


# ── What the sign-in screen is told ─────────────────────────────────


async def test_an_unconfigured_daemon_reports_no_authentication(server_factory):
    """Existing installs: no token, no account, nothing to sign in to."""
    client, _, _ = await server_factory(None)

    body = await (await client.get("/api/auth/session")).json()

    assert body["auth"] == "none"
    assert body["setup_available"] is False
    assert body["authenticated"] is True, "nothing is gated, so nothing is pending"


async def test_a_token_only_daemon_offers_setup(server_factory):
    client, _, _ = await server_factory(TOKEN)

    body = await (await client.get("/api/auth/session")).json()

    assert body["auth"] == "token"
    assert body["setup_available"] is True


async def test_an_account_daemon_reports_user_mode(server_factory):
    client, _, _ = await server_factory(TOKEN, account=True)

    body = await (await client.get("/api/auth/session")).json()

    assert body["auth"] == "user"
    assert body["setup_available"] is False, "an account already exists"
    assert body["authenticated"] is False
    assert body["username"] is None, "not before signing in"


async def test_the_session_endpoint_names_the_signed_in_user(server_factory):
    client, _, _ = await server_factory(TOKEN, account=True)
    assert (await _sign_in(client)).status == 204

    body = await (await client.get("/api/auth/session")).json()

    assert body["authenticated"] is True
    assert body["username"] == USERNAME


# ── Creating the account ────────────────────────────────────────────


async def test_setup_needs_the_access_token(server_factory):
    """The token is the proof that you are the operator, so this is not a claim page."""
    client, handler, _ = await server_factory(TOKEN)

    resp = await client.post(
        "/api/auth/setup",
        json={"token": "wrong", "username": USERNAME, "password": PASSWORD},
    )

    assert resp.status == 401
    assert handler.account is None, "an account was created without the token"


async def test_setup_with_the_token_creates_the_account_and_signs_you_in(server_factory):
    client, handler, _ = await server_factory(TOKEN)

    resp = await client.post(
        "/api/auth/setup",
        json={"token": TOKEN, "username": USERNAME, "password": PASSWORD},
    )

    assert resp.status == 204
    assert handler.account is not None
    assert "HttpOnly" in resp.headers.get("Set-Cookie", "")
    # The cookie works immediately: no second sign-in after creating an account.
    assert (await client.get("/api/status")).status == 200


async def test_setup_refuses_a_weak_password(server_factory):
    client, handler, _ = await server_factory(TOKEN)

    resp = await client.post(
        "/api/auth/setup", json={"token": TOKEN, "username": USERNAME, "password": "short"}
    )

    assert resp.status == 400
    assert "at least" in (await resp.json())["detail"]
    assert handler.account is None


async def test_setup_is_refused_once_an_account_exists(server_factory):
    """Otherwise anyone with the token could replace the account behind your back."""
    client, _, _ = await server_factory(TOKEN, account=True)

    resp = await client.post(
        "/api/auth/setup", json={"token": TOKEN, "username": "other", "password": PASSWORD}
    )

    assert resp.status == 409


async def test_setup_is_unavailable_without_a_token(server_factory):
    """An install with no token is already open; a setup page there is a claim page."""
    client, handler, _ = await server_factory(None)

    resp = await client.post(
        "/api/auth/setup", json={"token": "", "username": USERNAME, "password": PASSWORD}
    )

    assert resp.status == 403
    assert "user set" in (await resp.json())["detail"], "it should say where to go instead"
    assert handler.account is None


# ── Signing in ──────────────────────────────────────────────────────


async def test_the_right_password_signs_you_in(server_factory):
    client, _, _ = await server_factory(TOKEN, account=True)

    resp = await _sign_in(client)

    assert resp.status == 204
    assert (await client.get("/api/status")).status == 200


async def test_the_wrong_password_is_refused(server_factory):
    client, _, _ = await server_factory(TOKEN, account=True)

    resp = await _sign_in(client, password="not-the-password")

    assert resp.status == 401
    assert "Set-Cookie" not in resp.headers
    assert (await client.get("/api/status")).status == 401


async def test_a_wrong_username_looks_exactly_like_a_wrong_password(server_factory):
    """No enumeration: the response must not say which half was wrong."""
    client, _, _ = await server_factory(TOKEN, account=True)

    bad_user = await _sign_in(client, username="nobody")
    bad_pass = await _sign_in(client, password="nope")

    assert bad_user.status == bad_pass.status == 401
    assert await bad_user.json() == await bad_pass.json()


async def test_the_session_cookie_is_hardened(server_factory):
    client, _, _ = await server_factory(TOKEN, account=True)

    raw = (await _sign_in(client)).headers.get("Set-Cookie", "")

    assert auth.SESSION_COOKIE in raw
    assert "HttpOnly" in raw
    assert "SameSite=Lax" in raw
    assert "Secure" not in raw, "plain HTTP: a Secure cookie would never come back"


async def test_a_trusted_proxy_gets_a_secure_cookie(server_factory):
    client, _, _ = await server_factory(TOKEN, account=True, trust_proxy=True)

    resp = await client.post(
        "/api/auth/login",
        json={"username": USERNAME, "password": PASSWORD},
        headers={"X-Forwarded-Proto": "https"},
    )

    assert "Secure" in resp.headers.get("Set-Cookie", "")


async def test_an_untrusted_forwarded_header_is_ignored(server_factory):
    client, _, _ = await server_factory(TOKEN, account=True, trust_proxy=False)

    resp = await client.post(
        "/api/auth/login",
        json={"username": USERNAME, "password": PASSWORD},
        headers={"X-Forwarded-Proto": "https"},
    )

    assert "Secure" not in resp.headers.get("Set-Cookie", "")


async def test_repeated_failures_are_delayed(server_factory, monkeypatch):
    """The delay is what makes a memory-hard KDF affordable on an open endpoint."""
    import cloud_drive_sync.http.server as server_mod

    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(server_mod.asyncio, "sleep", fake_sleep)
    client, _, _ = await server_factory(TOKEN, account=True)

    for _ in range(auth.THROTTLE_FREE_ATTEMPTS + 3):
        await _sign_in(client, password="wrong")

    assert slept, "an attacker was never made to wait"
    assert max(slept) >= auth.THROTTLE_BASE_DELAY
    # And a correct password still works: this is a delay, never a lockout.
    assert (await _sign_in(client)).status == 204


async def test_signing_out_invalidates_the_session(server_factory):
    client, _, _ = await server_factory(TOKEN, account=True)
    await _sign_in(client)
    assert (await client.get("/api/status")).status == 200

    assert (await client.post("/api/auth/logout")).status == 204

    assert (await client.get("/api/status")).status == 401


# ── The token, once an account exists ───────────────────────────────


async def test_the_token_cookie_stops_working_once_an_account_exists(server_factory):
    """Decision 3, and the one most easily undone by a later refactor.

    A second browser path that bypasses the account would make the account
    decorative. The token stays a *machine* credential, so the bearer header keeps
    working — that is the next test.
    """
    client, handler, server = await server_factory(TOKEN)

    assert (await client.post("/api/auth/token", json={"token": TOKEN})).status == 204
    assert (await client.get("/api/status")).status == 200

    handler.install()
    server._forget_account()

    assert (await client.get("/api/status")).status == 401


async def test_the_bearer_token_still_works_once_an_account_exists(server_factory):
    """Deployed scripts, the Bruno collection and every curl example depend on it."""
    client, _, _ = await server_factory(TOKEN, account=True)

    resp = await client.get("/api/status", headers={"Authorization": f"Bearer {TOKEN}"})

    assert resp.status == 200


async def test_the_token_exchange_is_refused_once_an_account_exists(server_factory):
    client, _, _ = await server_factory(TOKEN, account=True)

    resp = await client.post("/api/auth/token", json={"token": TOKEN})

    assert resp.status == 409
    assert "Set-Cookie" not in resp.headers


async def test_an_account_alone_gates_the_api(server_factory):
    """No token at all, but an account exists: /api must still be protected."""
    client, _, _ = await server_factory(None, account=True)

    assert (await client.get("/api/status")).status == 401
    await _sign_in(client)
    assert (await client.get("/api/status")).status == 200


# ── CSRF ────────────────────────────────────────────────────────────


async def test_a_form_post_with_a_session_cookie_is_refused(server_factory):
    """The no-JavaScript vector: an HTML form cannot send JSON."""
    client, _, _ = await server_factory(TOKEN, account=True)
    await _sign_in(client)

    resp = await client.post("/api/sync", data={"pair_id": "0"})

    assert resp.status == 403
    assert "application/json" in (await resp.json())["detail"]


async def test_a_foreign_origin_with_a_session_cookie_is_refused(server_factory):
    client, _, _ = await server_factory(TOKEN, account=True)
    await _sign_in(client)

    resp = await client.post(
        "/api/sync", json={}, headers={"Origin": "http://evil.example"}
    )

    assert resp.status == 403


async def test_the_apps_own_requests_are_allowed(server_factory):
    client, _, server = await server_factory(TOKEN, account=True)
    await _sign_in(client)

    resp = await client.post("/api/sync", json={})

    assert resp.status == 200


async def test_a_bearer_caller_is_not_subject_to_the_csrf_rules(server_factory):
    """`curl -d` sends form encoding by default; breaking that would break scripts."""
    client, _, _ = await server_factory(TOKEN, account=True)

    resp = await client.post(
        "/api/sync",
        data={"pair_id": "0"},
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Origin": "http://somewhere.else",
        },
    )

    assert resp.status == 200


async def test_reads_are_not_blocked_by_the_origin_rule(server_factory):
    """A GET changes nothing, and blocking it would break an embedded dashboard."""
    client, _, _ = await server_factory(TOKEN, account=True)
    await _sign_in(client)

    resp = await client.get("/api/status", headers={"Origin": "http://elsewhere"})

    assert resp.status == 200


# ── Changing the password ───────────────────────────────────────────


async def test_the_password_can_be_changed(server_factory):
    client, handler, _ = await server_factory(TOKEN, account=True)
    await _sign_in(client)

    resp = await client.post(
        "/api/auth/password", json={"current": PASSWORD, "new": "a-brand-new-password"}
    )

    assert resp.status == 204
    assert auth.verify_password(handler.account["password"], "a-brand-new-password")


async def test_the_wrong_current_password_changes_nothing(server_factory):
    client, handler, _ = await server_factory(TOKEN, account=True)
    await _sign_in(client)

    resp = await client.post(
        "/api/auth/password", json={"current": "wrong", "new": "a-brand-new-password"}
    )

    assert resp.status == 401
    assert auth.verify_password(handler.account["password"], PASSWORD), "it changed anyway"


async def test_a_weak_new_password_is_refused(server_factory):
    client, _, _ = await server_factory(TOKEN, account=True)
    await _sign_in(client)

    resp = await client.post(
        "/api/auth/password", json={"current": PASSWORD, "new": "short"}
    )

    assert resp.status == 400


async def test_changing_the_password_signs_other_sessions_out(server_factory):
    """Otherwise whoever had a session keeps the access the old password gave them."""
    client, _, server = await server_factory(TOKEN, account=True)
    other_session = server._sessions.issue(USERNAME)
    await _sign_in(client)

    assert (
        await client.post(
            "/api/auth/password", json={"current": PASSWORD, "new": "a-brand-new-password"}
        )
    ).status == 204

    assert server._sessions.resolve(other_session) is None
    # The caller who changed it keeps working, on a fresh session.
    assert (await client.get("/api/status")).status == 200


async def test_changing_the_password_needs_a_credential(server_factory):
    client, _, _ = await server_factory(TOKEN, account=True)

    resp = await client.post(
        "/api/auth/password", json={"current": PASSWORD, "new": "a-brand-new-password"}
    )

    assert resp.status == 401


# ── The shell ───────────────────────────────────────────────────────


async def test_the_shell_is_served_so_the_spa_can_render_the_form(server_factory):
    client, _, _ = await server_factory(TOKEN, account=True)

    for path in ("/", "/login", "/settings"):
        resp = await client.get(path)
        assert resp.status == 200, path
        assert "text/html" in resp.headers["Content-Type"]


async def test_the_account_lookup_is_cached_rather_than_per_request(server_factory):
    """A database round trip per static asset would be a silly price for this."""
    client, handler, _ = await server_factory(TOKEN, account=True)

    for _ in range(5):
        await client.get("/api/auth/session")

    assert handler.calls.count("get_web_account") <= 2, handler.calls

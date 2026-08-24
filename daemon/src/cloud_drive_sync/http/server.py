"""HTTP REST API server wrapping the daemon's JSON-RPC handler."""

import asyncio
import json
import time
from dataclasses import asdict
from pathlib import Path

from aiohttp import web

from cloud_drive_sync.http import auth
from cloud_drive_sync.ipc.protocol import JsonRpcRequest
from cloud_drive_sync.util.logging import get_logger

log = get_logger("http.server")

WEBUI_DIR = Path(__file__).parent / "webui"

LOGIN_PATH = "/login"

#: Endpoints the sign-in screen itself needs, before anyone is signed in.
PUBLIC_API_PATHS = frozenset(
    {
        "/api/auth/session",
        "/api/auth/token",
        "/api/auth/login",
        "/api/auth/setup",
        "/api/auth/logout",
    }
)

#: Methods that cannot change anything, so they need no CSRF defence.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

#: How long the front-end may believe a cached answer to "is there an account?".
#: A lookup per request would be a database round trip on every static asset, and
#: an unbounded cache would miss an account created by the CLI while we run. Five
#: seconds bounds that staleness; our own writes invalidate immediately.
ACCOUNT_CACHE_SECONDS = 5.0

_NO_ACCOUNT = {"exists": False, "username": None}


class HttpServer:
    def __init__(
        self,
        handler,
        host: str = "0.0.0.0",
        port: int = 8080,
        auth_token: str | None = None,
        trust_proxy: bool = False,
    ) -> None:
        self._handler = handler
        self._host = host
        self._port = port
        self._auth_token = auth_token or None
        self._trust_proxy = trust_proxy
        # Sessions live here rather than in the database: they are mutable,
        # expiring and disposable, and the accepted cost is that restarting the
        # daemon signs you out. The account itself is a database row, reached
        # through the JSON-RPC handler — which is what keeps this class free of a
        # database handle.
        self._sessions = auth.SessionStore()
        self._throttle = auth.LoginThrottle()
        self._account_cache: tuple[float, dict] | None = None
        # Sticky: once an account has been seen, a *failed* lookup must not be
        # read as "no account configured". See _account().
        self._account_seen = False
        # Auth runs before CORS so an unauthorised request is rejected without the
        # handler being reached at all.
        self._app = web.Application(
            middlewares=[self._auth_middleware, self._cors_middleware]
        )
        self._runner: web.AppRunner | None = None
        self._setup_routes()

    # ── Authentication ──────────────────────────────────────────────
    #
    # Two credentials, layered. The token is the *machine* credential for /api/*
    # — deployed in systemd units and compose files, used by the Bruno collection
    # and every curl example — so it keeps working untouched. The account is the
    # *human* credential for the browser, carried by a session cookie.
    #
    # Both are accepted; neither replaces the other. See
    # docs/Proposal-Web-UI-Login.md for why replacing the token was rejected.

    async def _account(self, *, refresh: bool = False) -> dict:
        """Whether a web UI account exists, cached briefly. Never the hash.

        A failed lookup **fails closed** for an install that has one. Without
        that, a database error on an account-only daemon (no token) would answer
        "no account", the middleware would conclude nothing is configured, and the
        port would serve everything to anyone for as long as the error lasted —
        a guard that switches itself off under load is not a guard.

        It cannot fail closed unconditionally, though: an install that genuinely
        has no account would then be locked out of its own open UI by a transient
        error. So the memory is sticky in one direction only — once an account has
        been seen, a failure keeps requiring credentials; if none was ever seen, a
        failure leaves the previous behaviour alone.
        """
        now = time.monotonic()
        cached = self._account_cache
        if not refresh and cached and now - cached[0] < ACCOUNT_CACHE_SECONDS:
            return cached[1]
        state = await self._rpc_quiet("get_web_account")
        if not isinstance(state, dict):
            if self._account_seen:
                log.error(
                    "Could not read the web UI account; still requiring a "
                    "credential, because one was configured"
                )
                # Deliberately not cached: retry on the next request rather than
                # pinning a guess for the cache window.
                return {"exists": True, "username": None}
            state = _NO_ACCOUNT
        if state.get("exists"):
            self._account_seen = True
        self._account_cache = (now, state)
        return state

    def _forget_account(self) -> None:
        """Drop the cache after we change the account ourselves."""
        self._account_cache = None

    @web.middleware
    async def _auth_middleware(self, request, handler):
        """Require a credential when one is configured.

        Disabled entirely when there is neither a token nor an account, which is
        the default and preserves the pre-v2.4.0 behaviour exactly — an upgrade
        must never lock anyone out of a bookmarked URL.
        """
        # Preflight carries no credentials by design.
        if request.method == "OPTIONS":
            return await handler(request)

        account = await self._account()
        account_exists = bool(account.get("exists"))
        if self._auth_token is None and not account_exists:
            return await handler(request)

        path = request.path
        # Everything outside /api is the static bundle and the shell that renders
        # the sign-in screen. Serving it unauthenticated is what lets the SPA
        # *show* a sign-in form at all; there is no data in it, and /assets was
        # already public before this.
        if not path.startswith("/api/"):
            return await handler(request)
        if path in PUBLIC_API_PATHS:
            return await handler(request)

        kind, username = self._identify(request, account_exists=account_exists)
        if kind is None:
            return web.json_response(
                {"error": "unauthorized", "detail": "Sign in, or send a bearer token."},
                status=401,
                headers={"WWW-Authenticate": 'Bearer realm="cloud-drive-sync"'},
            )

        # CSRF applies only when a *cookie* is the credential: a bearer-token
        # caller cannot be a confused browser, and applying these rules to it
        # would break `curl -d` scripts that send no explicit content type.
        if kind == "session" and request.method not in SAFE_METHODS:
            problem = self._csrf_problem(request)
            if problem:
                log.warning("Refused a cross-site request to %s: %s", path, problem)
                return web.json_response(
                    {"error": "forbidden", "detail": problem}, status=403
                )

        request["cds_user"] = username
        return await handler(request)

    def _identify(self, request, *, account_exists: bool):
        """Resolve this request's credential to ``(kind, username)``.

        The two cookies have different names, and that is what keeps the
        credentials from being interchangeable: a session id offered as a bearer
        token is compared against the access token and fails, and the access token
        offered as a session id is looked up in the session store and is not
        there. No shared comparison to get wrong.
        """
        if self._auth_token is not None and auth.matches(
            self._auth_token, auth.token_from_headers(request.headers.get("Authorization"))
        ):
            return "token", None

        if not account_exists and self._auth_token is not None:
            # The pre-account browser path: the token in a cookie. Once an account
            # exists this stops being accepted, so the token goes back to being
            # purely a machine credential.
            if auth.matches(self._auth_token, request.cookies.get(auth.COOKIE_NAME)):
                return "token", None

        if account_exists:
            username = self._sessions.resolve(request.cookies.get(auth.SESSION_COOKIE))
            if username:
                return "session", username

        return None, None

    def _csrf_problem(self, request) -> str | None:
        """Why this cookie-authenticated mutation should be refused, if it should."""
        if auth.is_form_content_type(request.headers.get("Content-Type")):
            return "This endpoint accepts application/json only."
        if not auth.origin_is_same(
            request.headers.get("Origin"),
            request.headers.get("Referer"),
            request.headers.get("Host"),
        ):
            return "Origin does not match this daemon."
        return None

    def _client_address(self, request) -> str:
        """The peer address, for throttling. Never trusted for authorisation."""
        if self._trust_proxy:
            forwarded = request.headers.get("X-Forwarded-For")
            if forwarded:
                return forwarded.split(",")[0].strip()
        peer = request.remote
        return peer or "unknown"

    def _set_session_cookie(self, response, request, session_id: str) -> None:
        response.set_cookie(
            auth.SESSION_COOKIE,
            session_id,
            httponly=True,
            samesite="Lax",
            secure=auth.wants_secure_cookie(
                scheme=request.scheme,
                forwarded_proto=request.headers.get("X-Forwarded-Proto"),
                trust_proxy=self._trust_proxy,
            ),
            max_age=auth.SESSION_ABSOLUTE_SECONDS,
            path="/",
        )

    async def _auth_session(self, request):
        """What the sign-in screen needs in order to render, and nothing else."""
        account = await self._account()
        account_exists = bool(account.get("exists"))
        if account_exists:
            mode = "user"
        elif self._auth_token is not None:
            mode = "token"
        else:
            mode = "none"
        kind, username = self._identify(request, account_exists=account_exists)
        return self._json(
            {
                "auth": mode,
                # A token-only deployment is a valid steady state, not a half-built
                # one, so this offers the upgrade rather than demanding it.
                "setup_available": self._auth_token is not None and not account_exists,
                "authenticated": kind is not None or mode == "none",
                "username": username or (account.get("username") if kind else None),
            }
        )

    async def _auth_login(self, request):
        body = await self._body(request)
        username = str(body.get("username", "")).strip()
        password = str(body.get("password", ""))
        keys = (f"user:{username.lower()}", f"addr:{self._client_address(request)}")

        delay = self._throttle.delay_for(*keys)
        if delay:
            # Before the verification, not after: the point is to make the
            # attacker wait, and to keep the expensive part behind the delay.
            await asyncio.sleep(delay)

        if auth.waiting_for_slot():
            # Every hashing slot is busy. Refusing beats queueing: each waiting
            # attempt is a 16 MB allocation, and this endpoint is unauthenticated.
            return web.json_response(
                {"error": "busy", "detail": "Too many sign-in attempts at once."},
                status=503,
                headers={"Retry-After": "2"},
            )

        result = await self._rpc_quiet(
            "verify_web_account", {"username": username, "password": password}
        )
        if not (result or {}).get("ok"):
            self._throttle.record_failure(*keys)
            log.warning(
                "Failed web UI sign-in for %r from %s",
                username,
                self._client_address(request),
            )
            # One answer for a wrong username and a wrong password alike.
            return web.json_response(
                {"error": "invalid_credentials", "detail": "Sign-in failed."}, status=401
            )

        self._throttle.record_success(*keys)
        resolved = result.get("username") or username
        response = web.Response(status=204)
        self._set_session_cookie(response, request, self._sessions.issue(resolved))
        log.info("Web UI sign-in for %s from %s", resolved, self._client_address(request))
        return response

    async def _exchange_token(self, request):
        """Exchange the access token for a cookie — the pre-account browser path.

        This is the JSON replacement for the old form POST to ``/login``. It stays
        because a token-only deployment is a supported steady state, not a
        half-finished setup: someone with a bookmarked ``http://nas:8080`` and a
        token in their compose file must still be able to sign in after upgrading.

        Once an account exists it is refused, which is decision 3 in the proposal:
        from then on the token is purely a machine credential and a second way
        into the browser would bypass the account entirely.
        """
        if self._auth_token is None:
            return web.json_response(
                {"error": "unavailable", "detail": "This daemon has no access token."},
                status=403,
            )
        if (await self._account()).get("exists"):
            return web.json_response(
                {
                    "error": "account_configured",
                    "detail": "This daemon has an account. Sign in with it instead.",
                },
                status=409,
            )

        body = await self._body(request)
        addr = self._client_address(request)
        keys = (f"token:{addr}",)
        delay = self._throttle.delay_for(*keys)
        if delay:
            await asyncio.sleep(delay)
        if not auth.matches(self._auth_token, str(body.get("token", ""))):
            self._throttle.record_failure(*keys)
            log.warning("Rejected a wrong access token from %s", addr)
            # No detail about which part was wrong, and the value is never logged.
            return web.json_response(
                {"error": "invalid_token", "detail": "That token was not accepted."},
                status=401,
            )
        self._throttle.record_success(*keys)

        response = web.Response(status=204)
        response.set_cookie(
            auth.COOKIE_NAME,
            str(body.get("token", "")),
            httponly=True,
            samesite="Lax",
            secure=auth.wants_secure_cookie(
                scheme=request.scheme,
                forwarded_proto=request.headers.get("X-Forwarded-Proto"),
                trust_proxy=self._trust_proxy,
            ),
            max_age=60 * 60 * 24 * 30,
            path="/",
        )
        return response

    async def _auth_setup(self, request):
        """Create the account by presenting the access token.

        The token is the proof that you are the operator — first run generates it
        and prints it to stdout. That is why this is not an open claim page: on a
        daemon with no token there is no way to bootstrap over the network at all,
        and the CLI (over the local socket) is the only path.
        """
        account = await self._account(refresh=True)
        if account.get("exists"):
            return web.json_response(
                {"error": "already_configured", "detail": "An account already exists."},
                status=409,
            )
        if self._auth_token is None:
            return web.json_response(
                {
                    "error": "unavailable",
                    "detail": (
                        "This daemon has no access token, so an account cannot be "
                        "created from the browser. Run 'cloud-drive-sync user set "
                        "<name>' on the host instead."
                    ),
                },
                status=403,
            )

        body = await self._body(request)
        addr = self._client_address(request)
        key = (f"setup:{addr}",)
        delay = self._throttle.delay_for(*key)
        if delay:
            await asyncio.sleep(delay)
        if not auth.matches(self._auth_token, str(body.get("token", ""))):
            self._throttle.record_failure(*key)
            log.warning("Rejected account setup with a wrong token from %s", addr)
            return web.json_response(
                {"error": "invalid_token", "detail": "That token was not accepted."},
                status=401,
            )
        self._throttle.record_success(*key)

        result = await self._rpc_quiet(
            "set_web_account",
            {"username": body.get("username", ""), "password": body.get("password", "")},
        ) or {}
        if result.get("status") != "ok":
            return web.json_response(
                {
                    "error": result.get("status", "error"),
                    "detail": result.get("error", "Could not create the account."),
                },
                status=400,
            )

        self._forget_account()
        response = web.Response(status=204)
        self._set_session_cookie(
            response, request, self._sessions.issue(result["username"])
        )
        log.info("Web UI account created for %s from %s", result["username"], addr)
        return response

    async def _auth_logout(self, request):
        """Drop the presented session. Needs no credential of its own."""
        self._sessions.drop(request.cookies.get(auth.SESSION_COOKIE))
        response = web.Response(status=204)
        response.del_cookie(auth.SESSION_COOKIE, path="/")
        return response

    async def _auth_password(self, request):
        """Change the password, then invalidate every session.

        Dropping the sessions is the point: a password change that leaves the old
        password's sessions alive has not changed anything for whoever had one.
        """
        body = await self._body(request)
        result = await self._rpc_quiet(
            "change_web_password",
            {"current": body.get("current", ""), "new": body.get("new", "")},
        ) or {}
        status = result.get("status")
        if status == "ok":
            self._sessions.drop_all()
            self._forget_account()
            response = web.Response(status=204)
            # The caller who just changed it keeps working, on a fresh session.
            username = (await self._account(refresh=True)).get("username") or ""
            self._set_session_cookie(response, request, self._sessions.issue(username))
            return response
        if status == "invalid_credentials":
            return web.json_response(
                {"error": "invalid_credentials", "detail": "Current password is wrong."},
                status=401,
            )
        if status == "not_found":
            return web.json_response(
                {"error": "no_account", "detail": "No account is configured."},
                status=404,
            )
        return web.json_response(
            {"error": status or "error", "detail": result.get("error", "Failed.")},
            status=400,
        )

    @web.middleware
    async def _cors_middleware(self, request, handler):
        if request.method == "OPTIONS":
            response = web.Response()
        else:
            response = await handler(request)
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return response

    def _setup_routes(self):
        r = self._app.router
        # Status
        r.add_get("/api/status", self._get_status)
        # Accounts
        r.add_get("/api/accounts", self._list_accounts)
        r.add_post("/api/accounts", self._add_account)
        r.add_post("/api/accounts/auth-code", self._exchange_auth_code)
        r.add_get("/api/accounts/oauth-callback", self._oauth_callback)
        r.add_delete("/api/accounts/{email}", self._remove_account)
        r.add_put("/api/accounts/{email}/max-transfers", self._set_account_max_transfers)
        # Sync pairs
        r.add_get("/api/pairs", self._get_sync_pairs)
        r.add_post("/api/pairs", self._add_sync_pair)
        r.add_delete("/api/pairs/{pair_id}", self._remove_sync_pair)
        r.add_put("/api/pairs/{pair_id}/mode", self._set_sync_mode)
        r.add_put("/api/pairs/{pair_id}/ignore-hidden", self._set_ignore_hidden)
        r.add_put("/api/pairs/{pair_id}/ignore-patterns", self._set_ignore_patterns)
        r.add_get("/api/pairs/{pair_id}/rules", self._get_sync_rules)
        r.add_put("/api/pairs/{pair_id}/rules", self._set_sync_rules)
        r.add_put("/api/pairs/{pair_id}/conflict-strategy", self._set_pair_conflict_strategy)
        # Sync control
        r.add_post("/api/sync", self._force_sync)
        r.add_post("/api/sync/stop", self._emergency_stop)
        r.add_post("/api/sync/resume-stopped", self._emergency_resume)
        r.add_get("/api/sync/stop-state", self._get_stop_state)
        r.add_post("/api/sync/pause", self._pause_sync)
        r.add_post("/api/sync/resume", self._resume_sync)
        # Conflicts
        r.add_get("/api/conflicts", self._get_conflicts)
        r.add_post("/api/conflicts/{conflict_id}/resolve", self._resolve_conflict)
        # Activity
        r.add_get("/api/activity", self._get_activity_log)
        # Settings
        r.add_get("/api/settings/notifications", self._get_notification_prefs)
        r.add_put("/api/settings/notifications", self._set_notification_prefs)
        r.add_get("/api/settings/bandwidth", self._get_bandwidth_limits)
        r.add_put("/api/settings/bandwidth", self._set_bandwidth_limits)
        r.add_get("/api/settings/proxy", self._get_proxy)
        r.add_put("/api/settings/proxy", self._set_proxy)
        # Webhooks. The scope travels as a query parameter rather than a path
        # segment: a pair uid is fine in a path, but keeping one shape for every
        # scope avoids two routes that mean the same thing, and it leaves room for
        # the account scope (`?provider=..&email=..`) without a third.
        r.add_get("/api/webhooks", self._get_webhooks)
        r.add_put("/api/webhooks", self._set_webhooks)
        r.add_get("/api/webhooks/resolved", self._get_resolved_webhooks)
        r.add_get("/api/webhooks/status", self._get_webhook_status)
        r.add_post("/api/webhooks/test", self._test_webhook)
        r.add_put("/api/settings/conflict-strategy", self._set_conflict_strategy)
        r.add_post("/api/repair", self._repair)

        # Sign-in. Five endpoints and no user management, because there is one
        # account. /login itself is a client route now, served by the SPA shell.
        r.add_get("/api/auth/session", self._auth_session)
        r.add_post("/api/auth/token", self._exchange_token)
        r.add_post("/api/auth/setup", self._auth_setup)
        r.add_post("/api/auth/login", self._auth_login)
        r.add_post("/api/auth/logout", self._auth_logout)
        r.add_post("/api/auth/password", self._auth_password)

        r.add_get("/api/settings/max-deletions", self._get_max_deletions)
        r.add_put("/api/settings/max-deletions", self._set_max_deletions)
        r.add_get("/api/pending-deletions", self._get_pending_deletions)
        r.add_post("/api/pending-deletions/{pair_id}/resolve", self._resolve_pending_deletions)
        # Remote folders
        r.add_get("/api/remote-folders", self._list_remote_folders)
        r.add_post("/api/remote-folders", self._create_remote_folder)
        # Local filesystem browser (for headless/web UI)
        r.add_get("/api/local-dirs", self._list_local_dirs)
        r.add_post("/api/local-dirs", self._mkdir_local)
        # Web UI — serve React SPA from webui/ directory
        if WEBUI_DIR.exists():
            assets_dir = WEBUI_DIR / "assets"
            if assets_dir.exists():
                r.add_static("/assets", assets_dir)
            # SPA fallback: serve static root files if they exist, otherwise index.html
            r.add_get("/{path:.*}", self._serve_spa)

    async def _rpc(self, method: str, params: dict | None = None):
        """Call the JSON-RPC handler and return the result."""
        request = JsonRpcRequest(id=1, method=method, params=params or {})
        response = await self._handler.handle(request)
        if response.error:
            raise web.HTTPBadRequest(
                text=json.dumps({"error": asdict(response.error)}),
                content_type="application/json",
            )
        return response.result

    async def _rpc_quiet(self, method: str, params: dict | None = None):
        """Call the handler and return ``None`` on failure instead of raising.

        The sign-in path needs answers, not exceptions: a database that cannot say
        whether an account exists must read as "no account" and leave the token
        path working, rather than turning every request into a 400.
        """
        request = JsonRpcRequest(id=1, method=method, params=params or {})
        try:
            response = await self._handler.handle(request)
        except Exception:
            log.exception("Auth RPC %s failed", method)
            return None
        if response.error:
            log.warning("Auth RPC %s: %s", method, response.error)
            return None
        return response.result

    def _json(self, data):
        return web.json_response(data)

    async def _body(self, request) -> dict:
        try:
            return await request.json()
        except Exception:
            return {}

    # --- Route handlers ---
    async def _get_status(self, req): return self._json(await self._rpc("get_status"))
    async def _list_accounts(self, req): return self._json(await self._rpc("list_accounts"))
    async def _add_account(self, req): return self._json(await self._rpc("add_account", await self._body(req)))
    async def _exchange_auth_code(self, req): return self._json(await self._rpc("exchange_auth_code", await self._body(req)))
    async def _oauth_callback(self, req):
        """OAuth redirect callback — Google redirects here with ?code=..."""
        code = req.query.get("code", "")
        error = req.query.get("error", "")
        if error:
            return web.Response(
                text=f"<html><body style='background:#1c1c1e;color:#ff453a;font-family:sans-serif;padding:40px;text-align:center'>"
                     f"<h2>Authorization Failed</h2><p>{error}</p>"
                     f"<p>You can close this tab.</p></body></html>",
                content_type="text/html",
            )
        if not code:
            return web.Response(
                text="<html><body style='background:#1c1c1e;color:#ff453a;font-family:sans-serif;padding:40px;text-align:center'>"
                     "<h2>Missing authorization code</h2></body></html>",
                content_type="text/html",
            )
        # Exchange the code
        provider = req.query.get("state", "gdrive")
        result = await self._rpc("exchange_auth_code", {"provider": provider, "code": code})
        email = result.get("email", "") if isinstance(result, dict) else ""
        return web.Response(
            text=f"<html><body style='background:#1c1c1e;color:#30d158;font-family:sans-serif;padding:40px;text-align:center'>"
                 f"<h2>Authorization Successful</h2>"
                 f"<p>Account <strong>{email}</strong> has been added.</p>"
                 f"<p>You can close this tab and return to the dashboard.</p>"
                 f"<script>setTimeout(()=>window.close(),3000)</script></body></html>",
            content_type="text/html",
        )

    def get_oauth_callback_url(self) -> str:
        """Return the OAuth callback URL for this HTTP server."""
        return f"http://localhost:{self._port}/api/accounts/oauth-callback"

    async def _remove_account(self, req):
        params = {"email": req.match_info["email"]}
        if provider := req.rel_url.query.get("provider"):
            params["provider"] = provider
        return self._json(await self._rpc("remove_account", params))
    async def _set_account_max_transfers(self, req):
        body = await self._body(req)
        body["email"] = req.match_info["email"]
        return self._json(await self._rpc("set_account_max_transfers", body))
    async def _get_sync_pairs(self, req): return self._json(await self._rpc("get_sync_pairs"))
    async def _add_sync_pair(self, req): return self._json(await self._rpc("add_sync_pair", await self._body(req)))
    async def _remove_sync_pair(self, req): return self._json(await self._rpc("remove_sync_pair", {"id": req.match_info["pair_id"]}))
    async def _set_sync_mode(self, req):
        body = await self._body(req)
        body["pair_id"] = req.match_info["pair_id"]
        return self._json(await self._rpc("set_sync_mode", body))
    async def _set_ignore_hidden(self, req):
        body = await self._body(req)
        body["pair_id"] = req.match_info["pair_id"]
        return self._json(await self._rpc("set_ignore_hidden", body))
    async def _set_ignore_patterns(self, req):
        body = await self._body(req)
        body["pair_id"] = req.match_info["pair_id"]
        return self._json(await self._rpc("set_ignore_patterns", body))
    async def _get_sync_rules(self, req): return self._json(await self._rpc("get_sync_rules", {"pair_id": req.match_info["pair_id"]}))
    async def _set_sync_rules(self, req):
        body = await self._body(req)
        body["pair_id"] = req.match_info["pair_id"]
        return self._json(await self._rpc("set_sync_rules", body))
    async def _set_pair_conflict_strategy(self, req):
        body = await self._body(req)
        body["pair_id"] = req.match_info["pair_id"]
        return self._json(await self._rpc("set_pair_conflict_strategy", body))
    async def _repair(self, req): return self._json(await self._rpc("repair", await self._body(req)))
    async def _force_sync(self, req): return self._json(await self._rpc("force_sync", await self._body(req)))
    async def _pause_sync(self, req): return self._json(await self._rpc("pause_sync", await self._body(req)))
    async def _resume_sync(self, req): return self._json(await self._rpc("resume_sync", await self._body(req)))
    async def _get_conflicts(self, req): return self._json(await self._rpc("get_conflicts"))
    async def _resolve_conflict(self, req):
        body = await self._body(req)
        body["conflict_id"] = req.match_info["conflict_id"]
        return self._json(await self._rpc("resolve_conflict", body))
    async def _get_activity_log(self, req):
        params = {
            "limit": int(req.query.get("limit", 20)),
            "offset": int(req.query.get("offset", 0)),
            "filter": req.query.get("filter", "all"),
        }
        return self._json(await self._rpc("get_activity_log", params))
    async def _get_notification_prefs(self, req): return self._json(await self._rpc("get_notification_prefs"))
    async def _set_notification_prefs(self, req): return self._json(await self._rpc("set_notification_prefs", await self._body(req)))
    async def _get_bandwidth_limits(self, req): return self._json(await self._rpc("get_bandwidth_limits"))
    async def _set_bandwidth_limits(self, req): return self._json(await self._rpc("set_bandwidth_limits", await self._body(req)))
    def _webhook_params(self, req, body: dict | None = None) -> dict:
        """Merge the ``scope`` query parameter into the request body."""
        params = dict(body or {})
        params["scope"] = req.query.get("scope", "global")
        return params

    async def _get_webhooks(self, req):
        return self._json(await self._rpc("get_webhooks", self._webhook_params(req)))

    async def _set_webhooks(self, req):
        body = await self._body(req)
        return self._json(await self._rpc("set_webhooks", self._webhook_params(req, body)))

    async def _get_resolved_webhooks(self, req):
        return self._json(
            await self._rpc("get_resolved_webhooks", self._webhook_params(req))
        )

    async def _get_webhook_status(self, req):
        return self._json(await self._rpc("get_webhook_status", {}))

    async def _test_webhook(self, req):
        body = await self._body(req) if req.can_read_body else {}
        return self._json(await self._rpc("test_webhook", self._webhook_params(req, body)))

    async def _get_proxy(self, req): return self._json(await self._rpc("get_proxy"))
    async def _set_proxy(self, req): return self._json(await self._rpc("set_proxy", await self._body(req)))
    async def _set_conflict_strategy(self, req): return self._json(await self._rpc("set_conflict_strategy", await self._body(req)))
    async def _list_remote_folders(self, req):
        params = {"parent_id": req.query.get("parent_id", "root")}
        if "account_id" in req.query:
            params["account_id"] = req.query["account_id"]
        return self._json(await self._rpc("list_remote_folders", params))
    async def _create_remote_folder(self, req):
        body = await req.json()
        return self._json(await self._rpc("create_remote_folder", body))
    async def _emergency_stop(self, req):
        body = await self._body(req) if req.can_read_body else {}
        return self._json(await self._rpc("emergency_stop", body))

    async def _emergency_resume(self, req):
        body = await self._body(req) if req.can_read_body else {}
        return self._json(await self._rpc("emergency_resume", body))

    async def _get_stop_state(self, req):
        return self._json(await self._rpc("get_stop_state"))

    async def _get_max_deletions(self, req):
        return self._json(await self._rpc("get_max_deletions"))

    async def _set_max_deletions(self, req):
        return self._json(await self._rpc("set_max_deletions", await self._body(req)))

    async def _get_pending_deletions(self, req):
        params = {"pair_id": req.query.get("pair_id")} if req.query.get("pair_id") else {}
        return self._json(await self._rpc("get_pending_deletions", params))

    async def _resolve_pending_deletions(self, req):
        body = await self._body(req)
        body["pair_id"] = req.match_info["pair_id"]
        return self._json(await self._rpc("resolve_pending_deletions", body))

    async def _list_local_dirs(self, req):
        params = {"path": req.query.get("path", "")}
        return self._json(await self._rpc("list_local_dirs", params))
    async def _mkdir_local(self, req):
        return self._json(await self._rpc("mkdir_local", await self._body(req)))
    async def _serve_spa(self, req):
        """Serve static root files if they exist (e.g. favicon), otherwise SPA index."""
        path = req.match_info.get("path", "")
        if path:
            candidate = WEBUI_DIR / path
            if candidate.exists() and candidate.is_file() and WEBUI_DIR in candidate.parents:
                return web.FileResponse(candidate)
        return web.FileResponse(WEBUI_DIR / "index.html")

    async def start(self) -> None:
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._host, self._port)
        await site.start()
        log.info("HTTP server listening on http://%s:%d", self._host, self._port)
        account = await self._account(refresh=True)
        auth.warn_if_exposed(
            name="HTTP API and web UI",
            host=self._host,
            port=self._port,
            token=self._auth_token,
            account=bool(account.get("exists")),
        )

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
            log.info("HTTP server stopped")

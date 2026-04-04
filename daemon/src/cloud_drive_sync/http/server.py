"""HTTP REST API server wrapping the daemon's JSON-RPC handler."""

import json
from dataclasses import asdict
from pathlib import Path

from aiohttp import web

from cloud_drive_sync.ipc.protocol import JsonRpcRequest
from cloud_drive_sync.util.logging import get_logger

log = get_logger("http.server")

WEBUI_DIR = Path(__file__).parent / "webui"


class HttpServer:
    def __init__(self, handler, host: str = "0.0.0.0", port: int = 8080) -> None:
        self._handler = handler
        self._host = host
        self._port = port
        self._app = web.Application(middlewares=[self._cors_middleware])
        self._runner: web.AppRunner | None = None
        self._setup_routes()

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
        # Sync control
        r.add_post("/api/sync", self._force_sync)
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
        r.add_put("/api/settings/conflict-strategy", self._set_conflict_strategy)
        # Remote folders
        r.add_get("/api/remote-folders", self._list_remote_folders)
        # Web UI — serve index.html for all non-API routes
        if WEBUI_DIR.exists():
            r.add_get("/", self._serve_index)
            r.add_static("/static", WEBUI_DIR)

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
    async def _remove_account(self, req): return self._json(await self._rpc("remove_account", {"email": req.match_info["email"]}))
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
    async def _force_sync(self, req): return self._json(await self._rpc("force_sync", await self._body(req)))
    async def _pause_sync(self, req): return self._json(await self._rpc("pause_sync", await self._body(req)))
    async def _resume_sync(self, req): return self._json(await self._rpc("resume_sync", await self._body(req)))
    async def _get_conflicts(self, req): return self._json(await self._rpc("get_conflicts"))
    async def _resolve_conflict(self, req):
        body = await self._body(req)
        body["conflict_id"] = req.match_info["conflict_id"]
        return self._json(await self._rpc("resolve_conflict", body))
    async def _get_activity_log(self, req):
        params = {"limit": int(req.query.get("limit", 20)), "offset": int(req.query.get("offset", 0))}
        return self._json(await self._rpc("get_activity_log", params))
    async def _get_notification_prefs(self, req): return self._json(await self._rpc("get_notification_prefs"))
    async def _set_notification_prefs(self, req): return self._json(await self._rpc("set_notification_prefs", await self._body(req)))
    async def _get_bandwidth_limits(self, req): return self._json(await self._rpc("get_bandwidth_limits"))
    async def _set_bandwidth_limits(self, req): return self._json(await self._rpc("set_bandwidth_limits", await self._body(req)))
    async def _get_proxy(self, req): return self._json(await self._rpc("get_proxy"))
    async def _set_proxy(self, req): return self._json(await self._rpc("set_proxy", await self._body(req)))
    async def _set_conflict_strategy(self, req): return self._json(await self._rpc("set_conflict_strategy", await self._body(req)))
    async def _list_remote_folders(self, req):
        params = {"parent_id": req.query.get("parent_id", "root")}
        if "account_id" in req.query:
            params["account_id"] = req.query["account_id"]
        return self._json(await self._rpc("list_remote_folders", params))
    async def _serve_index(self, req):
        return web.FileResponse(WEBUI_DIR / "index.html")

    async def start(self) -> None:
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._host, self._port)
        await site.start()
        log.info("HTTP server listening on http://%s:%d", self._host, self._port)

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
            log.info("HTTP server stopped")

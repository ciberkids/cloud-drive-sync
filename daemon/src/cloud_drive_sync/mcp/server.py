"""MCP Streamable HTTP transport for the sync daemon.

A third front-end alongside the IPC socket and the HTTP/REST server, all three
dispatching to the same ``RequestHandler``. Only the transport lives here; what
the AI may do is decided in :mod:`cloud_drive_sync.mcp.catalog`, which imports no
SDK and is therefore testable without the optional extra installed.

Enabled with ``--mcp-port``; disabled by default everywhere, containers included.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

from cloud_drive_sync.http import auth as auth_mod
from cloud_drive_sync.mcp.catalog import ToolNotAvailable, dispatch, tools_for
from cloud_drive_sync.util.logging import get_logger

log = get_logger("mcp.server")

SERVER_NAME = "cloud-drive-sync"

# Shown to the assistant on connect, so it knows how to approach the tools rather
# than inferring it from names alone.
INSTRUCTIONS = """\
Manage and inspect a Cloud Drive Sync daemon syncing local folders with cloud \
storage (Google Drive, Nextcloud, Dropbox, OneDrive, Box).

Start with get_status for overall health, then get_activity_log with \
filter="error" to investigate failures, and get_file_status for a specific file \
that has not synced. Sync pairs are identified by pair_id like "pair_0"; most \
tools default to the first pair when it is omitted.

This server may be read-only. If a tool you need is not listed, the daemon was \
started without --mcp-allow-writes and the user must restart it to enable \
changes — say so rather than trying alternatives."""


def _daemon_version() -> str:
    try:
        from importlib.metadata import version

        return version("cloud-drive-sync")
    except Exception:
        from cloud_drive_sync import __version__

        return __version__

# The Host values accepted when DNS-rebinding protection is on. It has to be
# populated: the SDK's middleware rejects every request when this is empty.
DEFAULT_ALLOWED_HOSTS = (
    "localhost",
    "localhost:*",
    "127.0.0.1",
    "127.0.0.1:*",
    "[::1]",
    "[::1]:*",
)

# Opt out of host checking entirely, for reaching the server from another machine
# without naming it up front.
ANY_HOST = "*"


class McpServer:
    """Serves the daemon's tools over MCP Streamable HTTP at ``/mcp``."""

    def __init__(
        self,
        handler: Any,
        host: str = "0.0.0.0",
        port: int = 8081,
        allow_writes: bool = False,
        allowed_hosts: tuple[str, ...] | None = None,
        auth_token: str | None = None,
    ) -> None:
        self._handler = handler
        self._host = host
        self._port = port
        self._allow_writes = allow_writes
        self._auth_token = auth_token or None
        self._allowed_hosts = tuple(allowed_hosts) if allowed_hosts else DEFAULT_ALLOWED_HOSTS
        self._uvicorn: Any = None
        self._task: asyncio.Task | None = None

    # ── MCP protocol surface ────────────────────────────────────────

    def _build_mcp_app(self) -> Any:
        """Build the protocol server.

        mcp 2.x takes the handlers as constructor callbacks; the 1.x decorator API
        (``@server.list_tools()``) was removed, which is what broke CI when an
        unbounded ``mcp>=1.28.0`` floated onto 2.0.0.
        """
        import mcp.types as types
        from mcp.server.lowlevel import Server as LowLevelServer

        allow_writes = self._allow_writes
        handler = self._handler

        async def _on_list_tools(_ctx: Any, _params: Any) -> Any:
            return types.ListToolsResult(
                tools=[
                    types.Tool(
                        name=tool.name,
                        description=tool.description,
                        input_schema=tool.input_schema,
                    )
                    for tool in tools_for(allow_writes)
                ]
            )

        async def _on_call_tool(_ctx: Any, params: Any) -> Any:
            # 2.x wants an explicit result with is_error rather than letting an
            # exception propagate, so failures are reported rather than raised.
            try:
                result = await dispatch(
                    handler, params.name, params.arguments, allow_writes=allow_writes
                )
            except ToolNotAvailable as exc:
                return self._error_result(types, str(exc))
            except Exception as exc:
                log.exception("MCP tool %s failed", params.name)
                return self._error_result(types, str(exc))

            return types.CallToolResult(
                content=[
                    types.TextContent(
                        type="text", text=json.dumps(result, indent=2, default=str)
                    )
                ]
            )

        # Without an explicit version the SDK reports its own, so an assistant
        # would tell the user their daemon is version 2.0.0.
        return LowLevelServer(
            SERVER_NAME,
            version=_daemon_version(),
            instructions=INSTRUCTIONS,
            on_list_tools=_on_list_tools,
            on_call_tool=_on_call_tool,
        )

    @staticmethod
    def _error_result(types: Any, message: str) -> Any:
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=message)], is_error=True
        )

    def _build_asgi_app(self) -> Any:
        """The Starlette app for Streamable HTTP.

        mcp 2.x builds this itself, including the session manager and its lifespan
        — which in 1.x had to be wired by hand and failed per-request if forgotten.
        """
        from mcp.server.transport_security import TransportSecuritySettings

        # Origin headers are full URLs, Host headers are bare host:port. Passing
        # the host patterns straight through as origins means no Origin can ever
        # match, which silently rejects browser-based clients.
        origins = [
            f"{scheme}://{host}" for host in self._allowed_hosts for scheme in ("http", "https")
        ]

        if ANY_HOST in self._allowed_hosts:
            log.warning(
                "MCP host checking disabled (--mcp-allowed-host '*'); any Host header is "
                "accepted, so a browser on a reachable machine can drive this endpoint"
            )
            security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
        else:
            security = TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=list(self._allowed_hosts),
                allowed_origins=origins,
            )

        auth_mod.warn_if_exposed(
            name="MCP endpoint",
            host=self._host,
            port=self._port,
            token=self._auth_token,
        )
        # stateless: each request is self-contained, so there is no session state
        # to lose across a daemon restart and no event store to configure.
        app = self._build_mcp_app().streamable_http_app(
            streamable_http_path="/mcp",
            stateless_http=True,
            transport_security=security,
            host=self._host,
        )
        return self._with_auth(app)

    def _with_auth(self, app: Any) -> Any:
        """Wrap the app so every request must carry the shared token.

        Applied outside the SDK's app rather than via its token_verifier hook: that
        hook is shaped for OAuth, and a shared secret is what this daemon has. No
        token configured leaves the app untouched.
        """
        if self._auth_token is None:
            return app

        from starlette.middleware.base import BaseHTTPMiddleware
        from starlette.responses import JSONResponse

        expected = self._auth_token

        class _TokenMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                if auth_mod.is_authorised(
                    expected, authorization=request.headers.get("Authorization")
                ):
                    return await call_next(request)
                return JSONResponse(
                    {"error": "unauthorized", "detail": "A bearer token is required."},
                    status_code=401,
                    headers={"WWW-Authenticate": 'Bearer realm="cloud-drive-sync-mcp"'},
                )

        app.add_middleware(_TokenMiddleware)
        return app

    # ── Lifecycle ───────────────────────────────────────────────────

    async def start(self) -> None:
        """Start serving in a task owned by this object.

        ``uvicorn.run()`` calls ``asyncio.run()`` and cannot be used — the daemon
        is already inside a running loop.
        """
        import uvicorn

        config = uvicorn.Config(
            self._build_asgi_app(),
            host=self._host,
            port=self._port,
            log_config=None,  # keep our own logging configuration
            access_log=False,
        )
        self._uvicorn = uvicorn.Server(config)

        # Bind here, in the caller's coroutine, rather than letting the serve task do
        # it later. uvicorn reports a failed bind by calling sys.exit(), and a
        # SystemExit raised inside a background task is a BaseException that escapes
        # the caller's `except Exception` and unwinds the whole daemon — so a busy or
        # privileged MCP port took down sync as well, which is the opposite of the
        # intended "MCP is optional" behaviour. Translated to OSError so the caller can
        # treat it as the ordinary failure it is.
        try:
            sock = config.bind_socket()
        except SystemExit as exc:
            raise OSError(
                f"could not bind the MCP server to {self._host}:{self._port} "
                f"(port in use, or not permitted)"
            ) from exc

        # Only now is it true. This used to be logged while building the app, before
        # any bind was attempted, so a port that was busy still announced itself as
        # listening and the error came afterwards.
        log.info(
            "MCP server listening on http://%s:%d/mcp (%s, %d tools)",
            self._host,
            self._port,
            "read/write" if self._allow_writes else "read-only",
            len(tools_for(self._allow_writes)),
        )

        self._task = asyncio.create_task(self._uvicorn.serve(sockets=[sock]))
        # Anything the serve task raises after a successful bind is logged rather than
        # left to surface as an unretrieved task exception — or, for SystemExit, to
        # tear down the loop.
        self._task.add_done_callback(self._log_task_exit)

    @staticmethod
    def _log_task_exit(task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            log.error("MCP server stopped unexpectedly: %r", exc)

    async def stop(self) -> None:
        if self._uvicorn is not None:
            self._uvicorn.should_exit = True
        if self._task is not None:
            with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError):
                await asyncio.wait_for(self._task, timeout=10)
            self._task = None
        self._uvicorn = None
        log.info("MCP server stopped")

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
from collections.abc import AsyncIterator
from typing import Any

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
    ) -> None:
        self._handler = handler
        self._host = host
        self._port = port
        self._allow_writes = allow_writes
        self._allowed_hosts = tuple(allowed_hosts) if allowed_hosts else DEFAULT_ALLOWED_HOSTS
        self._uvicorn: Any = None
        self._task: asyncio.Task | None = None

    # ── MCP protocol surface ────────────────────────────────────────

    def _build_mcp_app(self) -> Any:
        from mcp import types
        from mcp.server.lowlevel import Server as LowLevelServer

        # Without an explicit version the SDK reports its own, so an assistant
        # would tell the user their daemon is version 1.28.1.
        server = LowLevelServer(SERVER_NAME, version=_daemon_version(), instructions=INSTRUCTIONS)
        allow_writes = self._allow_writes
        handler = self._handler

        @server.list_tools()
        async def _list_tools() -> list[types.Tool]:
            return [
                types.Tool(
                    name=tool.name,
                    description=tool.description,
                    inputSchema=tool.input_schema,
                )
                for tool in tools_for(allow_writes)
            ]

        @server.call_tool()
        async def _call_tool(name: str, arguments: dict | None) -> list[types.ContentBlock]:
            try:
                result = await dispatch(handler, name, arguments, allow_writes=allow_writes)
            except ToolNotAvailable as exc:
                # Raised so the SDK marks the result isError rather than the agent
                # having to detect failure from prose.
                raise ValueError(str(exc)) from exc
            text = json.dumps(result, indent=2, default=str)
            return [types.TextContent(type="text", text=text)]

        return server

    def _build_asgi_app(self) -> Any:
        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
        from mcp.server.transport_security import TransportSecuritySettings
        from starlette.applications import Starlette
        from starlette.routing import Mount

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
                allowed_origins=list(self._allowed_hosts),
            )

        # stateless: each request is self-contained, so there is no session state
        # to lose across a daemon restart and no event store to configure.
        manager = StreamableHTTPSessionManager(
            app=self._build_mcp_app(),
            stateless=True,
            security_settings=security,
        )

        async def _handle(scope, receive, send) -> None:
            await manager.handle_request(scope, receive, send)

        @contextlib.asynccontextmanager
        async def lifespan(_app: Starlette) -> AsyncIterator[None]:
            # Required: the manager's task group is created here, and without it
            # every request fails individually instead of failing at startup.
            async with manager.run():
                log.info(
                    "MCP server listening on http://%s:%d/mcp (%s, %d tools)",
                    self._host,
                    self._port,
                    "read/write" if self._allow_writes else "read-only",
                    len(tools_for(self._allow_writes)),
                )
                yield

        return Starlette(lifespan=lifespan, routes=[Mount("/mcp", app=_handle)])

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
        self._task = asyncio.create_task(self._uvicorn.serve())

    async def stop(self) -> None:
        if self._uvicorn is not None:
            self._uvicorn.should_exit = True
        if self._task is not None:
            with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError):
                await asyncio.wait_for(self._task, timeout=10)
            self._task = None
        self._uvicorn = None
        log.info("MCP server stopped")

"""Transport-level tests for the MCP server.

Exercised against a real MCP client over Streamable HTTP, because the failure
modes here are protocol-level and invisible to unit tests: the session manager
needs a lifespan context, and uvicorn must not be started with `uvicorn.run()`
inside an already-running loop.

`mcp` is in the `dev` extra, so these run in CI as well as in the project venv.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("mcp", reason="mcp extra not installed")
pytest.importorskip("uvicorn", reason="mcp extra not installed")

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from cloud_drive_sync.ipc.protocol import JsonRpcResponse
from cloud_drive_sync.mcp.catalog import READ_TOOLS, WRITE_TOOLS
from cloud_drive_sync.mcp.server import McpServer

# Ports are per-test to avoid collisions when tests run close together.
BASE_PORT = 18300


class FakeHandler:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def handle(self, request):
        self.calls.append((request.method, request.params))
        return JsonRpcResponse.success(request.id, {"method": request.method, "params": request.params})


class _Running:
    def __init__(self, server: McpServer, url: str, handler: FakeHandler) -> None:
        self.server = server
        self.url = url
        self.handler = handler


async def _serve(port: int, **kwargs) -> _Running:
    handler = FakeHandler()
    server = McpServer(handler, host="127.0.0.1", port=port, **kwargs)
    await server.start()
    # Give uvicorn a moment to bind before the client connects.
    for _ in range(50):
        await asyncio.sleep(0.1)
        if server._uvicorn is not None and getattr(server._uvicorn, "started", False):
            break
    return _Running(server, f"http://127.0.0.1:{port}/mcp", handler)


@pytest.fixture
async def read_only_server():
    running = await _serve(BASE_PORT)
    yield running
    await running.server.stop()


@pytest.fixture
async def write_server():
    running = await _serve(BASE_PORT + 1, allow_writes=True)
    yield running
    await running.server.stop()


async def test_client_can_initialize(read_only_server):
    """Covers the two silent transport failures at once: without the session
    manager's lifespan context, or with uvicorn.run() in a live loop, this
    cannot complete."""
    async with (
            streamable_http_client(read_only_server.url) as (r, w),
            ClientSession(r, w) as session,
        ):
            result = await session.initialize()

    assert result.server_info.name == "cloud-drive-sync"


async def test_server_reports_the_daemon_version_not_the_sdk_version(read_only_server):
    """The SDK defaults server_info.version to its own, so an assistant would tell
    the user their daemon is version 1.28.1."""
    import mcp

    from cloud_drive_sync.mcp.server import _daemon_version

    async with (
            streamable_http_client(read_only_server.url) as (r, w),
            ClientSession(r, w) as session,
        ):
            result = await session.initialize()

    assert result.server_info.version == _daemon_version()
    assert result.server_info.version != getattr(mcp, "__version__", None)


async def test_server_sends_usage_instructions(read_only_server):
    """Tells the assistant how to approach the tools, including that a missing
    write tool means the daemon needs restarting rather than a workaround."""
    async with (
            streamable_http_client(read_only_server.url) as (r, w),
            ClientSession(r, w) as session,
        ):
            result = await session.initialize()

    assert result.instructions
    assert "get_status" in result.instructions
    assert "--mcp-allow-writes" in result.instructions


async def test_read_only_server_advertises_only_read_tools(read_only_server):
    async with (
            streamable_http_client(read_only_server.url) as (r, w),
            ClientSession(r, w) as session,
        ):
            await session.initialize()
            listed = await session.list_tools()

    names = {t.name for t in listed.tools}
    assert names == {t.name for t in READ_TOOLS}
    assert "remove_account" not in names
    assert "force_sync" not in names


async def test_write_server_advertises_write_tools(write_server):
    async with (
            streamable_http_client(write_server.url) as (r, w),
            ClientSession(r, w) as session,
        ):
            await session.initialize()
            listed = await session.list_tools()

    names = {t.name for t in listed.tools}
    assert {t.name for t in WRITE_TOOLS} <= names
    assert "force_sync" in names


async def test_calling_a_tool_reaches_the_request_handler(read_only_server):
    async with (
            streamable_http_client(read_only_server.url) as (r, w),
            ClientSession(r, w) as session,
        ):
            await session.initialize()
            result = await session.call_tool("get_activity_log", {"limit": 5, "filter": "error"})

    assert result.is_error is not True
    assert "get_activity_log" in result.content[0].text
    assert ("get_activity_log", {"limit": 5, "filter": "error"}) in read_only_server.handler.calls


async def test_tool_name_is_translated_to_the_handler_method(read_only_server):
    async with (
            streamable_http_client(read_only_server.url) as (r, w),
            ClientSession(r, w) as session,
        ):
            await session.initialize()
            await session.call_tool("list_sync_pairs", {})

    assert ("get_sync_pairs", {}) in read_only_server.handler.calls


async def test_write_tool_on_read_only_server_errors_and_explains(read_only_server):
    """It is not advertised, but a client can still name it directly."""
    async with (
            streamable_http_client(read_only_server.url) as (r, w),
            ClientSession(r, w) as session,
        ):
            await session.initialize()
            result = await session.call_tool("force_sync", {})

    assert result.is_error is True
    assert "--mcp-allow-writes" in result.content[0].text
    assert read_only_server.handler.calls == [], "gated tool reached RequestHandler"


async def test_tool_schemas_survive_the_round_trip(read_only_server):
    """The model chooses arguments from these, so they must arrive intact."""
    async with (
            streamable_http_client(read_only_server.url) as (r, w),
            ClientSession(r, w) as session,
        ):
            await session.initialize()
            listed = await session.list_tools()

    by_name = {t.name: t for t in listed.tools}
    activity = by_name["get_activity_log"]
    assert activity.input_schema["properties"]["limit"]["type"] == "integer"
    assert "error" in activity.input_schema["properties"]["filter"]["enum"]

    file_status = by_name["get_file_status"]
    assert file_status.input_schema["required"] == ["path"]


async def test_stop_is_idempotent_and_releases_the_port():
    """A second daemon start must not fail because the port is still held."""
    running = await _serve(BASE_PORT + 2)
    await running.server.stop()
    await running.server.stop()  # must not raise

    again = await _serve(BASE_PORT + 2)
    try:
        async with (
                streamable_http_client(again.url) as (r, w),
                ClientSession(r, w) as session,
            ):
                assert (await session.initialize()).server_info.name == "cloud-drive-sync"
    finally:
        await again.server.stop()


async def test_disabled_by_default_in_the_daemon():
    """--mcp-port defaults to 0, so no MCP port opens on upgrade."""
    from cloud_drive_sync.daemon import Daemon

    daemon = Daemon()
    assert daemon._mcp_port == 0
    assert daemon._mcp_allow_writes is False
    assert daemon._mcp_server is None


def test_allowed_origins_are_derived_as_urls_not_reused_as_hosts():
    """Origin headers are full URLs; Host headers are bare host:port.

    Feeding the host patterns straight through as origins means no Origin can
    ever match, which silently rejects browser-based clients — and the transport
    tests above would still pass, because MCP clients send no Origin at all.
    """
    from mcp.server.transport_security import (
        TransportSecurityMiddleware,
        TransportSecuritySettings,
    )

    from cloud_drive_sync.mcp.server import DEFAULT_ALLOWED_HOSTS

    origins = [f"{s}://{h}" for h in DEFAULT_ALLOWED_HOSTS for s in ("http", "https")]
    mw = TransportSecurityMiddleware(
        TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=list(DEFAULT_ALLOWED_HOSTS),
            allowed_origins=origins,
        )
    )

    assert mw._validate_origin("http://localhost:8081") is True
    assert mw._validate_origin("https://127.0.0.1:8081") is True
    # A foreign origin must still be refused.
    assert mw._validate_origin("http://evil.example:8081") is False


# ── Token authentication (opt-in) ───────────────────────────────────


async def test_mcp_without_a_token_is_open():
    """Default behaviour, unchanged — enabling auth by default would break every
    existing MCP client configuration on upgrade."""
    running = await _serve(BASE_PORT + 3)
    try:
        async with (
            streamable_http_client(running.url) as (r, w),
            ClientSession(r, w) as session,
        ):
            assert (await session.initialize()).server_info.name == "cloud-drive-sync"
    finally:
        await running.server.stop()


async def test_mcp_with_a_token_refuses_anonymous_clients():
    """An unauthenticated MCP endpoint exposes the same write surface as the REST
    API, including switching off delete protection when writes are enabled."""
    import httpx2 as httpx

    running = await _serve(BASE_PORT + 4, auth_token="mcp-secret")
    try:
        async with httpx.AsyncClient() as http:
            resp = await http.post(
                running.url,
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                headers={"Accept": "application/json, text/event-stream"},
            )
        assert resp.status_code == 401
        assert "Bearer" in resp.headers.get("WWW-Authenticate", "")
        assert running.handler.calls == []
    finally:
        await running.server.stop()


async def test_mcp_accepts_the_correct_token():
    running = await _serve(BASE_PORT + 5, auth_token="mcp-secret")
    try:
        import httpx2

        # 2.x takes a prepared client rather than headers, so credentials go on the
        # client itself.
        async with (
            httpx2.AsyncClient(
            headers={"Authorization": "Bearer mcp-secret"}
        ) as authed, streamable_http_client(running.url, http_client=authed) as (r, w),
            ClientSession(r, w) as session,
        ):
            listed = await session.list_tools()
            assert listed.tools, "authorised client got no tools"
    finally:
        await running.server.stop()


async def test_mcp_refuses_a_wrong_token():
    import httpx2 as httpx

    running = await _serve(BASE_PORT + 6, auth_token="mcp-secret")
    try:
        async with httpx.AsyncClient() as http:
            resp = await http.post(
                running.url,
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                headers={
                    "Accept": "application/json, text/event-stream",
                    "Authorization": "Bearer wrong",
                },
            )
        assert resp.status_code == 401
    finally:
        await running.server.stop()


def test_an_empty_mcp_token_counts_as_none():
    """An unset environment variable arrives as "", which must not enable auth with
    a secret that every request satisfies."""
    assert McpServer(FakeHandler(), auth_token="")._auth_token is None

"""Tests for the MCP tool catalogue.

Imports no `mcp` SDK, so these run everywhere — including CI, which installs a
subset of extras. Transport-level tests live in test_mcp_server.py.
"""

from __future__ import annotations

import pytest

from cloud_drive_sync.ipc.protocol import JsonRpcResponse
from cloud_drive_sync.mcp import catalog
from cloud_drive_sync.mcp.catalog import (
    ALL_TOOLS,
    NEVER_EXPOSED,
    READ_TOOLS,
    WRITE_TOOLS,
    ToolNotAvailable,
    dispatch,
    lookup,
    tools_for,
)


class FakeHandler:
    """Records what reached RequestHandler, and can be told to fail."""

    def __init__(self, error: str | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._error = error

    async def handle(self, request):
        self.calls.append((request.method, request.params))
        if self._error:
            return JsonRpcResponse.fail(request.id, -32603, self._error)
        return JsonRpcResponse.success(request.id, {"ok": True, "method": request.method})


# ── Write gating ────────────────────────────────────────────────────


def test_read_only_mode_advertises_no_write_tools():
    """Absent from tools/list, not advertised-then-refused.

    An agent that sees remove_account will call it; a refusal it could not have
    predicted from the tool list just burns turns.
    """
    names = {t.name for t in tools_for(allow_writes=False)}

    assert names == {t.name for t in READ_TOOLS}
    for tool in WRITE_TOOLS:
        assert tool.name not in names


def test_write_mode_advertises_everything():
    names = {t.name for t in tools_for(allow_writes=True)}

    assert names == {t.name for t in ALL_TOOLS}
    assert "remove_account" in names
    assert "force_sync" in names


def test_no_tool_is_marked_read_and_write_inconsistently():
    for tool in READ_TOOLS:
        assert tool.writes is False, f"{tool.name} is in READ_TOOLS but marked writes=True"
    for tool in WRITE_TOOLS:
        assert tool.writes is True, f"{tool.name} is in WRITE_TOOLS but marked writes=False"


def test_read_tools_only_map_to_getters():
    """A read tool wired to a mutating method would silently defeat the gate."""
    for tool in READ_TOOLS:
        assert tool.method.startswith(("get_", "list_")), (
            f"read tool {tool.name} dispatches to {tool.method}, which is not a getter"
        )


@pytest.mark.parametrize("name", [t.name for t in WRITE_TOOLS])
def test_write_tools_are_rejected_in_read_only_mode(name):
    with pytest.raises(ToolNotAvailable) as exc:
        lookup(name, allow_writes=False)

    # The message must tell the agent how to proceed, not just say no.
    assert "--mcp-allow-writes" in str(exc.value)


def test_unknown_tool_is_distinguishable_from_a_gated_one():
    with pytest.raises(ToolNotAvailable, match="Unknown tool"):
        lookup("definitely_not_a_tool", allow_writes=True)


# ── Exclusions ──────────────────────────────────────────────────────


def test_never_exposed_methods_are_absent_from_every_tool():
    """shutdown, OAuth code exchange, proxy config and host filesystem browsing
    must not be reachable at any permission level."""
    exposed_methods = {t.method for t in ALL_TOOLS}

    for method, reason in NEVER_EXPOSED.items():
        assert method not in exposed_methods, f"{method} is exposed but excluded because: {reason}"


def test_exclusion_list_names_real_handler_methods():
    """Guards against the list rotting into a set of typos that assert nothing."""
    from cloud_drive_sync.ipc.handlers import RequestHandler

    source = __import__("inspect").getsource(RequestHandler.__init__)
    for method in NEVER_EXPOSED:
        assert f'"{method}"' in source, (
            f"{method} is in NEVER_EXPOSED but is not a RequestHandler method — "
            "the exclusion protects nothing"
        )


def test_secrets_and_shutdown_specifically_excluded():
    for method in ("shutdown", "exchange_auth_code", "get_proxy", "set_proxy", "list_local_dirs"):
        assert method in NEVER_EXPOSED


# ── Catalogue integrity ─────────────────────────────────────────────


def test_tool_names_are_unique():
    names = [t.name for t in ALL_TOOLS]
    assert len(names) == len(set(names))


def test_every_tool_maps_to_a_real_handler_method():
    from cloud_drive_sync.ipc.handlers import RequestHandler

    source = __import__("inspect").getsource(RequestHandler.__init__)
    for tool in ALL_TOOLS:
        assert f'"{tool.method}"' in source, (
            f"tool {tool.name} dispatches to {tool.method}, which RequestHandler does not implement"
        )


@pytest.mark.parametrize("tool", ALL_TOOLS, ids=lambda t: t.name)
def test_schemas_are_well_formed(tool):
    schema = tool.input_schema
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    for name in schema["required"]:
        assert name in schema["properties"], f"{tool.name} requires undeclared property {name}"


@pytest.mark.parametrize("tool", ALL_TOOLS, ids=lambda t: t.name)
def test_descriptions_are_useful(tool):
    """Descriptions are the only thing the model sees when choosing a tool."""
    assert len(tool.description) > 40, f"{tool.name} description is too thin to choose on"
    assert tool.description[0].isupper()


def test_destructive_tools_say_what_they_do_not_touch():
    """Removing a pair or account must not read as deleting files."""
    for name in ("remove_sync_pair", "remove_account", "repair"):
        tool = next(t for t in ALL_TOOLS if t.name == name)
        assert "not delete" in tool.description.lower() or "never" in tool.description.lower(), (
            f"{name} should state that it does not delete files"
        )


# ── Dispatch ────────────────────────────────────────────────────────


async def test_dispatch_forwards_to_the_handler_method():
    handler = FakeHandler()

    result = await dispatch(handler, "get_activity_log", {"limit": 5}, allow_writes=False)

    assert handler.calls == [("get_activity_log", {"limit": 5})]
    assert result == {"ok": True, "method": "get_activity_log"}


async def test_dispatch_maps_tool_name_to_handler_method_when_they_differ():
    """list_sync_pairs -> get_sync_pairs; a wrong mapping would 'work' but be silent."""
    handler = FakeHandler()

    await dispatch(handler, "list_sync_pairs", None, allow_writes=False)

    assert handler.calls == [("get_sync_pairs", {})]


async def test_dispatch_refuses_a_write_tool_without_reaching_the_handler():
    handler = FakeHandler()

    with pytest.raises(ToolNotAvailable):
        await dispatch(handler, "remove_account", {"email": "a@b.c"}, allow_writes=False)

    assert handler.calls == [], "a gated tool must not reach RequestHandler at all"


async def test_dispatch_surfaces_handler_errors():
    handler = FakeHandler(error="No sync pairs configured")

    with pytest.raises(RuntimeError, match="No sync pairs configured"):
        await dispatch(handler, "get_status", {}, allow_writes=False)


async def test_dispatch_treats_missing_arguments_as_empty():
    handler = FakeHandler()

    await dispatch(handler, "get_status", None, allow_writes=False)

    assert handler.calls == [("get_status", {})]


def test_default_allowed_hosts_are_loopback_only():
    """The SDK rejects everything when allowed_hosts is empty, and accepts anything
    when protection is off — so the default has to be populated and narrow."""
    from cloud_drive_sync.mcp.server import ANY_HOST, DEFAULT_ALLOWED_HOSTS

    assert DEFAULT_ALLOWED_HOSTS, "empty allowed_hosts rejects every request"
    assert ANY_HOST not in DEFAULT_ALLOWED_HOSTS
    for host in DEFAULT_ALLOWED_HOSTS:
        assert host.startswith(("localhost", "127.0.0.1", "[::1]")), host


def test_catalog_imports_without_the_mcp_sdk():
    """The catalogue must stay SDK-free so it is testable wherever CI runs."""
    import sys

    src = __import__("inspect").getsource(catalog)
    assert "import mcp" not in src
    assert "from mcp" not in src
    assert "mcp.server" not in src
    # Importing it must not have pulled the SDK in as a side effect.
    assert "cloud_drive_sync.mcp.catalog" in sys.modules


# ── CLI / environment wiring ────────────────────────────────────────


def _invoke(env: dict) -> dict:
    """Run `start --foreground` with env set, capturing what Daemon received."""
    from click.testing import CliRunner

    import cloud_drive_sync.daemon as daemon_module
    from cloud_drive_sync.cli import cli

    captured: dict = {}

    class FakeDaemon:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        @staticmethod
        def is_running():
            return False

        async def run(self):
            return None

    original = daemon_module.Daemon
    daemon_module.Daemon = FakeDaemon
    try:
        result = CliRunner().invoke(cli, ["start", "--foreground"], env=env)
        assert result.exit_code == 0, result.output
    finally:
        daemon_module.Daemon = original
    return captured


def test_mcp_is_off_unless_asked_for():
    assert _invoke({})["mcp_port"] == 0


def test_container_env_vars_enable_mcp():
    """Docker/Quadlet configure this purely through the environment."""
    captured = _invoke({"CDS_MCP_PORT": "8081", "CDS_MCP_ALLOW_WRITES": "1"})

    assert captured["mcp_port"] == 8081
    assert captured["mcp_allow_writes"] is True


def test_allow_writes_env_var_is_off_for_zero():
    assert _invoke({"CDS_MCP_ALLOW_WRITES": "0"})["mcp_allow_writes"] is False


def test_allowed_hosts_env_var_splits_on_whitespace():
    """Documented footgun: commas are not a separator.

    A comma-separated value arrives as one malformed host, which matches nothing
    and would reject every request — so the docs say spaces, and this pins it.
    """
    spaced = _invoke({"CDS_MCP_ALLOWED_HOSTS": "a.local:* b.local:*"})
    assert spaced["mcp_allowed_hosts"] == ("a.local:*", "b.local:*")

    commas = _invoke({"CDS_MCP_ALLOWED_HOSTS": "a.local:*,b.local:*"})
    assert commas["mcp_allowed_hosts"] == ("a.local:*,b.local:*",)


def test_mcp_host_can_be_restricted_to_loopback():
    assert _invoke({"CDS_MCP_HOST": "127.0.0.1"})["mcp_host"] == "127.0.0.1"


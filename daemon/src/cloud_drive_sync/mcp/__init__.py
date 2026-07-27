"""MCP (Model Context Protocol) front-end for the sync daemon.

``catalog`` is always importable; ``server`` needs the optional ``mcp`` extra and
is imported lazily by the daemon so a build without it still starts.
"""

from cloud_drive_sync.mcp.catalog import (
    ALL_TOOLS,
    NEVER_EXPOSED,
    READ_TOOLS,
    WRITE_TOOLS,
    McpTool,
    ToolNotAvailable,
    dispatch,
    lookup,
    tools_for,
)

__all__ = [
    "ALL_TOOLS",
    "NEVER_EXPOSED",
    "READ_TOOLS",
    "WRITE_TOOLS",
    "McpTool",
    "ToolNotAvailable",
    "dispatch",
    "lookup",
    "tools_for",
]


def is_available() -> bool:
    """Whether the optional ``mcp`` extra is installed.

    Checked without importing, so this stays cheap and has no import side effects.
    """
    from importlib.util import find_spec

    return find_spec("mcp") is not None and find_spec("uvicorn") is not None

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
    """Whether the optional ``mcp`` extra is installed."""
    try:
        import mcp  # noqa: F401
        import uvicorn  # noqa: F401
    except ImportError:
        return False
    return True
